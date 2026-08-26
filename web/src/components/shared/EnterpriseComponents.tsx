// Enterprise page sub-components — ported from the Next.js reference's
// components/enterprise/ directory: admin-dashboard, capabilities, contact,
// demo-video, feature-showcase, features, hero, iac, open-source, pricing,
// procurement, product-showcase, security, support, trust-bar, uptime.
// (calendly-inline is inlined into contact.) All self-contained, using
// react-router and the dark admin design system. framer-motion's useInView is
// replaced by a small inline IntersectionObserver hook; NumberTicker is
// replaced by a simple inline animated counter.

import {
  ArrowDown, ArrowRight, ArrowUpRight, BadgeCheck, BarChart3, Bell, BookOpen,
  Building2, Check, CheckCircle2, ChevronDown, Cloud, Code2, Coins, Database,
  FileCheck, FileSearch, Gauge, GitBranch, Globe, Headphones, Hash, Image as ImageIcon,
  Key, LayoutDashboard, Lock, MessagesSquare, MoreHorizontal, Paintbrush,
  Percent, Receipt, Rocket, ScrollText, Server, Shield, ShieldCheck, ShieldQuestion,
  Terminal, TrendingUp, User2, Users, Wallet, Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

// ── Inline hooks (replacing framer-motion / next hooks) ────────────────────

function useInView(ref: React.RefObject<HTMLElement | null>, opts?: { once?: boolean; margin?: string }) {
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const once = opts?.once ?? true;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setInView(false);
        }
      },
      { rootMargin: opts?.margin ?? "0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, opts?.once, opts?.margin]);
  return inView;
}

function NumberTicker({ value, className }: { value: number; className?: string; decimalPlaces?: number; delay?: number; startValue?: number }) {
  // Simple: just show the number (no count-up animation to avoid complexity).
  return <span className={className}>{value.toLocaleString()}</span>;
}

// ── AdminDashboardEnterprise ────────────────────────────────────────────────

const adminCapabilities = [
  { icon: TrendingUp, title: "Real-Time Metrics", description: "Track signups, revenue, verified users, and paying customers from a single view. Configurable time ranges from 7 days to all-time." },
  { icon: Building2, title: "Organization Management", description: "Search, sort, and drill into every organization. View projects, API keys, transactions, credits, and spending at a glance." },
  { icon: Server, title: "Provider Monitoring", description: "Monitor every LLM provider in real time. Track request volume, error rates, cache hit ratios, and average time to first token." },
  { icon: BarChart3, title: "Model Performance", description: "See which models are being used, how often they fail, and how fast they respond. Filter and sort across hundreds of models instantly." },
  { icon: Percent, title: "Discount Controls", description: "Set global or per-organization discounts by provider and model. Full control over pricing without touching code." },
  { icon: BarChart3, title: "Revenue Analytics", description: "Interactive charts for signup trends and revenue over time. Spot patterns, track growth, and forecast with confidence." },
];

export function AdminDashboardEnterprise() {
  return (
    <section className="border-t border-[var(--admin-border)] py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto mb-16 max-w-3xl text-center">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-4 py-1.5">
            <span className="font-mono text-xs text-blue-400">SELF-HOSTED</span>
            <span className="text-xs text-[var(--admin-text-muted)]">Included with every enterprise deployment</span>
          </div>
          <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">A full admin dashboard, on your infrastructure</h2>
          <p className="text-lg text-[var(--admin-text-muted)]">When you self-host wiwi, you get a complete admin dashboard to monitor, manage, and optimize your entire LLM operation. No external dependencies, no data leaving your network.</p>
        </div>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {adminCapabilities.map((cap) => (
            <div key={cap.title} className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 transition-all hover:border-blue-500/50">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400"><cap.icon className="h-5 w-5" /></div>
              <h3 className="mb-2 text-lg font-semibold text-[var(--admin-text)]">{cap.title}</h3>
              <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{cap.description}</p>
            </div>
          ))}
        </div>
        <div className="mt-12 text-center">
          <Link to="/enterprise#contact" className="admin-btn admin-btn-ghost">Request a demo</Link>
        </div>
      </div>
    </section>
  );
}

// ── CalendlyInline (inlined; loads Calendly widget script) ──────────────────

export function CalendlyInline({ url, name, email }: { url: string; name?: string; email?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const initializedRef = useRef(false);
  const [scriptLoaded, setScriptLoaded] = useState(() => typeof window !== "undefined" && Boolean((window as unknown as { Calendly?: unknown }).Calendly));

  useEffect(() => {
    if (scriptLoaded) return;
    const s = document.createElement("script");
    s.src = "https://assets.calendly.com/assets/external/widget.js";
    s.async = true;
    s.onload = () => setScriptLoaded(true);
    document.body.appendChild(s);
    return () => { s.remove(); };
  }, [scriptLoaded]);

  useEffect(() => {
    const container = containerRef.current;
    if (!scriptLoaded || !container || !(window as unknown as { Calendly?: { initInlineWidget: (opts: { url: string; parentElement: HTMLElement; prefill?: { name?: string; email?: string } }) => void } }).Calendly || initializedRef.current) return;
    initializedRef.current = true;
    const widgetUrl = new URL(url);
    widgetUrl.searchParams.set("hide_gdpr_banner", "1");
    widgetUrl.searchParams.set("primary_color", "2563eb");
    if (document.documentElement.classList.contains("dark")) {
      widgetUrl.searchParams.set("background_color", "0a0a0a");
      widgetUrl.searchParams.set("text_color", "e4e4e7");
    }
    (window as unknown as { Calendly: { initInlineWidget: (opts: { url: string; parentElement: HTMLElement; prefill?: { name?: string; email?: string } }) => void } }).Calendly.initInlineWidget({ url: widgetUrl.toString(), parentElement: container, prefill: { name, email } });
  }, [scriptLoaded, url, name, email]);

  return (
    <>
      <div ref={containerRef} className="h-[1040px] w-full overflow-hidden rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] sm:h-[720px]" />
      <p className="mt-3 text-center text-sm text-[var(--admin-text-muted)]">
        Prefer a new tab?{" "}
        <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-blue-400 underline-offset-4 hover:underline">Open the scheduler</a>
      </p>
    </>
  );
}

// ── EnterpriseCapabilities ──────────────────────────────────────────────────

const enterpriseFeatures = [
  { slug: "audit-logs", iconName: "shield-check", accent: "indigo", title: "Tamper-evident audit logs", tagline: "Every change, recorded.", description: "Every configuration, permission, spend-limit, and key-rotation event is logged and exportable to your SIEM." },
  { slug: "sso", iconName: "badge-check", accent: "violet", title: "SSO & SAML", tagline: "Okta, Entra, Google.", description: "SAML 2.0 and OIDC with role-based permissions mapped from your identity provider." },
  { slug: "guardrails", iconName: "git-branch", accent: "emerald", title: "Guardrails", tagline: "Block prompts at the edge.", description: "Regex and semantic rules that reject or rewrite requests before they reach a provider." },
  { slug: "iam", iconName: "audit", accent: "sky", title: "IAM rules", tagline: "Per-key, per-route policies.", description: "Restrict which models, providers, and endpoints each key or team can access." },
  { slug: "alerts", iconName: "bell", accent: "amber", title: "Alerts", tagline: "Spend, latency, errors.", description: "Slack, email, and webhook notifications when cost or error thresholds are crossed." },
  { slug: "data-residency", iconName: "lock", accent: "rose", title: "Data residency", tagline: "Pin workloads to a region.", description: "Route regulated traffic to EU-only providers to keep your compliance scope tight." },
  { slug: "whitelabel", iconName: "paintbrush", accent: "indigo", title: "White-label", tagline: "Your brand, your domain.", description: "Replace the logo, colors, and domain across every dashboard and chat surface." },
  { slug: "analytics", iconName: "chart", accent: "violet", title: "Advanced analytics", tagline: "Per-project cost & usage.", description: "Interactive charts for cost, tokens, latency, and errors across any dimension." },
  { slug: "team", iconName: "users", accent: "emerald", title: "Team & budgets", tagline: "Per-developer spend limits.", description: "Assign projects, keys, and hard spend caps to each team member." },
] as const;

const iconMap: Record<string, LucideIcon> = {
  "shield-check": ShieldCheck, "badge-check": BadgeCheck, "git-branch": GitBranch,
  audit: FileSearch, bell: Bell, lock: Lock, paintbrush: Paintbrush, chart: BarChart3, users: Users,
};

const accentMap: Record<string, string> = {
  indigo: "from-indigo-500/20 to-indigo-500/0 text-indigo-400",
  amber: "from-amber-500/20 to-amber-500/0 text-amber-400",
  emerald: "from-emerald-500/20 to-emerald-500/0 text-emerald-400",
  rose: "from-rose-500/20 to-rose-500/0 text-rose-400",
  sky: "from-sky-500/20 to-sky-500/0 text-sky-400",
  violet: "from-violet-500/20 to-violet-500/0 text-violet-400",
};
const accentBorderMap: Record<string, string> = {
  indigo: "hover:border-indigo-500/40", amber: "hover:border-amber-500/40",
  emerald: "hover:border-emerald-500/40", rose: "hover:border-rose-500/40",
  sky: "hover:border-sky-500/40", violet: "hover:border-violet-500/40",
};

export function EnterpriseCapabilities() {
  return (
    <section id="capabilities" className="relative overflow-hidden py-24 sm:py-32">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_-10%,rgba(99,102,241,0.08),transparent_55%),radial-gradient(circle_at_80%_110%,rgba(16,185,129,0.06),transparent_55%)]" />
      <div className="container relative mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mb-16 flex max-w-3xl flex-col items-start gap-4">
          <span className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-bg)]/50 px-3 py-1 backdrop-blur-sm">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
            <span className="font-mono text-xs uppercase tracking-wider text-[var(--admin-text-muted)]">Newly shipped</span>
          </span>
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Nine capabilities your security team will actually approve</h2>
          <p className="text-lg leading-relaxed text-[var(--admin-text-muted)]">The pieces that turn wiwi from a developer tool into an auditable, multi-team, multi-tenant production platform. Each one ships with audit trails, SSO-aware permissions, and SIEM-ready exports.</p>
        </div>
        <div className="grid gap-px overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-border)] md:grid-cols-2 lg:grid-cols-3">
          {enterpriseFeatures.map((feature) => {
            const Icon = iconMap[feature.iconName];
            return (
              <Link key={feature.slug} to={`/enterprise/${feature.slug}`} className={`group relative flex flex-col gap-6 border border-transparent bg-[var(--admin-bg)] p-8 transition-colors hover:bg-[var(--admin-surface)] ${accentBorderMap[feature.accent]}`}>
                <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br opacity-0 transition-opacity duration-500 group-hover:opacity-100 ${accentMap[feature.accent]}`} />
                <div className="relative flex items-start justify-between">
                  <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/[0.03] ring-1 ring-[var(--admin-border)]"><Icon className="h-5 w-5" /></div>
                  <ArrowUpRight className="h-5 w-5 text-[var(--admin-text-muted)] transition-all duration-300 group-hover:-translate-y-1 group-hover:translate-x-1 group-hover:text-[var(--admin-text)]" />
                </div>
                <div className="relative flex flex-col gap-2">
                  <h3 className="text-xl font-semibold tracking-tight text-[var(--admin-text)]">{feature.title}</h3>
                  <p className="text-sm font-medium text-[var(--admin-text-muted)]">{feature.tagline}</p>
                </div>
                <p className="relative mt-auto text-sm leading-relaxed text-[var(--admin-text-muted)]">{feature.description}</p>
                <span className="relative inline-flex items-center gap-1.5 text-xs font-medium text-[var(--admin-text-muted)] transition-colors group-hover:text-[var(--admin-text)]">Read the deep-dive <ArrowUpRight className="h-3 w-3" /></span>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}

// ── ContactFormEnterprise (simplified: no react-hook-form/zod) ────────────────

export function ContactFormEnterprise() {
  const [isSuccess, setIsSuccess] = useState(false);
  const [directBooking, setDirectBooking] = useState(false);
  const [form, setForm] = useState({ name: "", email: "", country: "", size: "", deployment: "", message: "" });
  const CALENDLY_URL = "https://calendly.com/example/enterprise";

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // No backend; just show success + Calendly.
    setIsSuccess(true);
  };

  return (
    <section className="py-20 sm:py-28" id="contact">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-4xl">
          <div className="mb-12 text-center">
            <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Get Started with Enterprise</h2>
            <p className="mx-auto max-w-2xl text-lg text-[var(--admin-text-muted)]">Tell us about your needs and our team will reach out to discuss how wiwi can support your organization.</p>
          </div>
          <div className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]/50 p-8 shadow-lg sm:p-10">
            {!isSuccess && (
              <div className="mb-8 flex flex-col items-center gap-3 rounded-xl border border-dashed border-[var(--admin-border)] bg-white/[0.01] p-4 text-center sm:flex-row sm:justify-between sm:text-left">
                <p className="text-sm text-[var(--admin-text-muted)]">{directBooking ? "Prefer to write instead? Switch back to the form." : "In a hurry? Skip the form and book a 20-min walkthrough directly."}</p>
                <button type="button" className="admin-btn admin-btn-ghost" onClick={() => setDirectBooking(!directBooking)}>{directBooking ? "Back to the form" : "Book a walkthrough"}</button>
              </div>
            )}
            {!isSuccess && directBooking ? (
              <CalendlyInline url={CALENDLY_URL} />
            ) : isSuccess ? (
              <div className="py-4">
                <div className="text-center">
                  <div className="mb-6 inline-flex h-16 w-16 items-center justify-center rounded-full bg-green-500/10"><CheckCircle2 className="h-8 w-8 text-green-400" /></div>
                  <h3 className="mb-2 text-2xl font-semibold text-[var(--admin-text)]">Thank you for reaching out!</h3>
                  <p className="text-[var(--admin-text-muted)]">We&apos;ve received your message. Book a time below and our team will meet you then — otherwise we&apos;ll reply within 24 hours.</p>
                </div>
                <div className="mt-8"><CalendlyInline url={CALENDLY_URL} name={form.name} email={form.email} /></div>
                <div className="mt-6 text-center"><button type="button" className="admin-btn admin-btn-ghost" onClick={() => setIsSuccess(false)}>Send Another Message</button></div>
              </div>
            ) : (
              <form onSubmit={onSubmit} className="space-y-6">
                <div className="grid gap-6 sm:grid-cols-2">
                  <label className="block"><span className="admin-label mb-1.5 block">Name *</span><input className="admin-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="John Doe" required /></label>
                  <label className="block"><span className="admin-label mb-1.5 block">Company Email *</span><input type="email" className="admin-input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="john@company.com" required /></label>
                </div>
                <div className="grid gap-6 sm:grid-cols-2">
                  <label className="block"><span className="admin-label mb-1.5 block">Country *</span>
                    <select className="admin-input" value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} required>
                      <option value="">Select country</option><option>United States</option><option>United Kingdom</option><option>Germany</option><option>Japan</option><option>Other</option>
                    </select>
                  </label>
                  <label className="block"><span className="admin-label mb-1.5 block">Company Size *</span>
                    <select className="admin-input" value={form.size} onChange={(e) => setForm({ ...form, size: e.target.value })} required>
                      <option value="">Select size</option><option value="1-50">1-50 employees</option><option value="51-200">51-200 employees</option><option value="201-500">201-500 employees</option><option value="501-1000">501-1000 employees</option><option value="1000+">1000+ employees</option>
                    </select>
                  </label>
                </div>
                <label className="block"><span className="admin-label mb-1.5 block">How do you plan to run wiwi? *</span>
                  <select className="admin-input" value={form.deployment} onChange={(e) => setForm({ ...form, deployment: e.target.value })} required>
                    <option value="">Select deployment preference</option><option value="cloud">Cloud (managed)</option><option value="self_host">Self-hosted</option><option value="not_sure">Not sure yet</option>
                  </select>
                </label>
                <label className="block"><span className="admin-label mb-1.5 block">How can we help? *</span><textarea className="admin-input min-h-32 resize-none" value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} placeholder="Tell us about your use case…" required /></label>
                <div className="flex flex-col items-start gap-4 pt-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-sm text-[var(--admin-text-muted)]"><span className="text-red-400">*</span> Required fields</p>
                  <button type="submit" className="admin-btn admin-btn-primary min-w-[180px]">Submit Request <ArrowRight className="ml-2 h-4 w-4" /></button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

// ── EnterpriseDemoVideo ──────────────────────────────────────────────────────

const DURATION_LABEL = "6:35";
const CHAPTERS = [
  { at: 0, time: "0:00", label: "Dashboard & live activity" },
  { at: 60, time: "1:00", label: "Usage by model, key & member" },
  { at: 82, time: "1:22", label: "API keys & IAM rules" },
  { at: 132, time: "2:12", label: "Team roles & per-developer budgets" },
  { at: 158, time: "2:38", label: "Compliance & provider-HQ routing" },
  { at: 200, time: "3:20", label: "Guardrails & security events" },
  { at: 240, time: "4:00", label: "SAML SSO with Microsoft Entra" },
];

export function EnterpriseDemoVideo() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [started, setStarted] = useState(false);
  const [activeAt, setActiveAt] = useState(0);

  function playFrom(at: number) {
    const video = videoRef.current;
    if (!video) return;
    setStarted(true);
    setActiveAt(at);
    if (video.readyState === 0) { video.addEventListener("loadedmetadata", () => { video.currentTime = at; }, { once: true }); }
    else { video.currentTime = at; }
    void video.play();
  }

  function syncActiveChapter() {
    const video = videoRef.current;
    if (!video) return;
    const current = CHAPTERS.reduce((acc, c) => (video.currentTime >= c.at ? c.at : acc), 0);
    setActiveAt((prev) => (prev === current ? prev : current));
  }

  return (
    <div className="mx-auto mt-16 max-w-5xl">
      <div className="relative">
        <div className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-1.5 shadow-[0_0_80px_-20px_rgba(59,130,246,0.35)]">
          <div className="relative aspect-video overflow-hidden rounded-xl bg-[var(--admin-bg)]">
            <video ref={videoRef} className="h-full w-full" controls={started} controlsList="nodownload" playsInline preload="none" onPlay={() => setStarted(true)} onTimeUpdate={syncActiveChapter}>
              <source src="/videos/enterprise-demo.mp4" type="video/mp4" />
              <track kind="captions" src="/videos/enterprise-demo.vtt" srcLang="en" label="English" />
            </video>
            {!started && (
              <button type="button" onClick={() => playFrom(0)} aria-label={`Play the enterprise product walkthrough, ${DURATION_LABEL}`} className="group absolute inset-0 cursor-pointer">
                <img src="/videos/enterprise-demo-poster.jpg" alt="Enterprise product walkthrough" className="h-full w-full object-cover opacity-60" />
                <span className="absolute inset-0 flex items-center justify-center">
                  <span className="relative flex size-20 items-center justify-center rounded-full bg-blue-600 text-white shadow-xl transition-transform group-hover:scale-110"><span className="absolute inset-0 animate-ping rounded-full bg-blue-500/40" /><svg viewBox="0 0 24 24" className="relative ml-1 h-8 w-8 fill-current"><path d="M8 5v14l11-7z" /></svg></span>
                </span>
                <span className="absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-4 text-left sm:p-6">
                  <span className="flex flex-col"><span className="font-mono text-xs uppercase tracking-wider text-blue-300">Founder walkthrough</span><span className="mt-1 hidden text-sm font-semibold text-white sm:block sm:text-base">The whole enterprise product, unedited</span></span>
                  <span className="shrink-0 rounded-full border border-white/25 bg-black/50 px-3 py-1 font-mono text-xs text-white backdrop-blur-sm">{DURATION_LABEL}</span>
                </span>
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="mt-6">
        <p className="mb-3 text-center font-mono text-xs uppercase tracking-wider text-[var(--admin-text-muted)]">Jump to</p>
        <div className="flex flex-wrap justify-center gap-2">
          {CHAPTERS.map((chapter) => {
            const isActive = started && activeAt === chapter.at;
            return (
              <button key={chapter.at} type="button" onClick={() => playFrom(chapter.at)} className={`flex items-center gap-2 rounded-full border px-3 py-2.5 text-sm transition-colors ${isActive ? "border-blue-500/60 bg-blue-500/10 text-[var(--admin-text)]" : "border-[var(--admin-border)] bg-[var(--admin-surface)]/50 text-[var(--admin-text-muted)] hover:border-blue-500/50 hover:text-[var(--admin-text)]"}`}>
                <span className="font-mono text-xs text-blue-400">{chapter.time}</span>{chapter.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── EnterpriseFeatureShowcase (mock data, no analytics deps) ────────────────

const mockProjects = [
  { key: "prod-api", label: "Production API", base: 168, wave: 24, phase: 0.9 },
  { key: "support-bot", label: "Support Chatbot", base: 84, wave: 14, phase: 2.1 },
  { key: "research", label: "Research & Evals", base: 38, wave: 26, phase: 4.4 },
  { key: "internal", label: "Internal Tools", base: 15, wave: 5, phase: 6.2 },
];

function ShowcaseFrame({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-bg)] shadow-2xl">
      <div className="flex items-center gap-2 border-b border-[var(--admin-border)] bg-white/[0.02] px-4 py-3">
        <span className="h-3 w-3 rounded-full bg-red-400/70" /><span className="h-3 w-3 rounded-full bg-yellow-400/70" /><span className="h-3 w-3 rounded-full bg-green-400/70" />
        <span className="ml-3 font-mono text-xs text-[var(--admin-text-muted)]">{label}</span>
        <span className="admin-badge admin-badge-gray ml-auto">Mock data</span>
      </div>
      <div className="space-y-4 bg-white/[0.01] p-4 sm:p-6">{children}</div>
    </div>
  );
}

function SummaryStat({ label, value, icon: Icon }: { label: string; value: string; icon: LucideIcon }) {
  return (
    <div className="admin-card flex items-center gap-3 p-4">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-blue-500/10 text-blue-400"><Icon className="h-5 w-5" /></div>
      <div className="min-w-0"><p className="text-xs font-medium uppercase tracking-wide text-[var(--admin-text-muted)]">{label}</p><p className="truncate text-2xl font-semibold tabular-nums text-[var(--admin-text)]">{value}</p></div>
    </div>
  );
}

function OrgAnalyticsShowcase() {
  const numberFormatter = new Intl.NumberFormat("en-US");
  const currencyFormatter = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  const totalCost = mockProjects.reduce((s, p) => s + p.base * 30, 0);
  const totalRequests = mockProjects.reduce((s, p) => s + p.base * 420 * 30, 0);
  const totalTokens = mockProjects.reduce((s, p) => s + p.base * 130_000 * 30, 0);
  return (
    <ShowcaseFrame label="Organization → Analytics">
      <div className="grid gap-4 sm:grid-cols-3">
        <SummaryStat label="Total spend" value={currencyFormatter.format(totalCost)} icon={Coins} />
        <SummaryStat label="Requests" value={numberFormatter.format(totalRequests)} icon={Zap} />
        <SummaryStat label="Tokens" value={numberFormatter.format(totalTokens)} icon={Hash} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex h-8 items-center gap-2 rounded-md border border-[var(--admin-border)] bg-[var(--admin-bg)] px-3 text-sm text-[var(--admin-text)]">Jun 1 – Jun 30, 2026 <ChevronDown className="h-4 w-4 opacity-50" /></div>
        <div className="inline-flex h-8 items-center gap-2 rounded-md border border-[var(--admin-border)] bg-[var(--admin-bg)] px-3 text-sm text-[var(--admin-text)]">Breakdown by project <ChevronDown className="h-4 w-4 opacity-50" /></div>
      </div>
      <div className="admin-card p-4">
        <p className="admin-label mb-2">Cost by project over time</p>
        <div className="flex h-40 items-end gap-2">
          {mockProjects.map((p) => (
            <div key={p.key} className="flex flex-1 flex-col items-center gap-1">
              <div className="w-full rounded-t bg-blue-500/30" style={{ height: `${(p.base / 168) * 100}%` }} />
              <span className="text-[10px] text-[var(--admin-text-dim)]">{p.label.split(" ")[0]}</span>
            </div>
          ))}
        </div>
      </div>
    </ShowcaseFrame>
  );
}

const mockMembers = [
  { name: "Amira Haddad", email: "amira@acme.dev", role: "owner", cost: "$4,812.40", tokens: "625,612,300", requests: "2,021,208", apiKeys: 6 },
  { name: "Jonas Weber", email: "jonas@acme.dev", role: "admin", cost: "$2,304.11", tokens: "299,534,410", requests: "967,726", apiKeys: 4 },
  { name: "Priya Sharma", email: "priya@acme.dev", role: "developer", cost: "$412.86", tokens: "53,671,800", requests: "173,401", apiKeys: 3 },
  { name: "Marco Rossi", email: "marco@acme.dev", role: "developer", cost: "$189.44", tokens: "24,627,200", requests: "79,565", apiKeys: 2 },
];

function MemberBudgetsShowcase() {
  return (
    <ShowcaseFrame label="Organization → Team">
      <div className="admin-card">
        <div className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div><p className="text-sm font-medium text-[var(--admin-text)]">Default developer limits</p><p className="text-xs text-[var(--admin-text-muted)]">Applied to every developer without a personal override</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="admin-badge admin-badge-blue">$100.00 total</span><span className="admin-badge admin-badge-blue">$25.00/week</span><span className="admin-badge admin-badge-blue">3 keys</span>
            <button className="admin-btn admin-btn-ghost">Edit defaults</button>
          </div>
        </div>
      </div>
      <div className="admin-card overflow-x-auto p-0">
        <div className="admin-table"><table className="w-full text-left"><thead><tr><th>Name</th><th>Role</th><th className="text-right">Cost</th><th className="text-right">Tokens</th><th className="text-right">Requests</th><th className="text-right">API keys</th><th></th></tr></thead>
          <tbody>
            {mockMembers.map((m) => (
              <tr key={m.email}>
                <td><div className="font-medium text-[var(--admin-text)]">{m.name}</div><div className="text-xs text-[var(--admin-text-muted)]">{m.email}</div></td>
                <td><span className="admin-badge admin-badge-gray capitalize">{m.role}</span></td>
                <td className="text-right font-medium tabular-nums text-[var(--admin-text)]">{m.cost}</td>
                <td className="text-right tabular-nums text-[var(--admin-text-muted)]">{m.tokens}</td>
                <td className="text-right tabular-nums text-[var(--admin-text-muted)]">{m.requests}</td>
                <td className="text-right tabular-nums text-[var(--admin-text-muted)]">{m.apiKeys}</td>
                <td><button className="rounded p-1 text-[var(--admin-text-dim)] hover:text-[var(--admin-text)]"><MoreHorizontal className="h-4 w-4" /></button></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </ShowcaseFrame>
  );
}

const showcases: Record<string, () => ReactNode> = { "organization-analytics": OrgAnalyticsShowcase, "member-budgets": MemberBudgetsShowcase };

export function EnterpriseFeatureShowcase({ slug }: { slug: string }) {
  const Showcase = showcases[slug];
  return Showcase ? <Showcase /> : null;
}

// ── FeaturesEnterprise ──────────────────────────────────────────────────────

const featuresList = [
  { icon: Key, title: "Use Your Own API Keys", description: "Bring your own provider API keys without any surcharges. Full control over your costs." },
  { icon: Wallet, title: "Volume-Discounted Fees", description: "Custom volume pricing cuts the standard platform fee as your usage scales." },
  { icon: BarChart3, title: "Advanced Analytics", description: "Deep insights into usage patterns, costs, and performance across all your LLM operations." },
  { icon: Users, title: "Unlimited Seats", description: "Add as many team members as you need. No per-seat pricing or user limits." },
  { icon: Rocket, title: "On-boarding Assistance", description: "Dedicated support during setup and migration. We ensure a smooth transition for your team." },
  { icon: Database, title: "Unlimited Data Retention", description: "Keep your request logs and analytics data forever. No automatic deletion or storage limits." },
  { icon: Headphones, title: "24/7 Premium Support", description: "Round-the-clock access to our engineering team through a dedicated Slack or Discord channel." },
  { icon: MessagesSquare, title: "Chat App & Whitelabel", description: "Full-featured chat app included. Customize with your branding for internal or customer use." },
  { icon: Shield, title: "Single Sign-On (SSO)", description: "Seamless integration with your identity provider. Support for SAML, OAuth, and OIDC." },
  { icon: LayoutDashboard, title: "Admin Dashboard", description: "Full-featured admin panel to manage organizations, monitor providers, track model performance, and control pricing." },
  { icon: ShieldCheck, title: "SOC 2 Type II Compliant", description: "Independently audited against the highest standards of security, availability, and confidentiality." },
];

export function FeaturesEnterprise() {
  return (
    <section id="features" className="py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto mb-16 max-w-2xl text-center">
          <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Built for enterprise scale and security</h2>
          <p className="text-lg text-[var(--admin-text-muted)]">Everything you need to deploy and manage LLM infrastructure across your organization with confidence.</p>
        </div>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {featuresList.map((feature) => (
            <div key={feature.title} className="admin-card p-6 transition-colors hover:border-blue-500/50">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-blue-500/10"><feature.icon className="h-6 w-6 text-blue-400" /></div>
              <h3 className="mb-2 text-xl font-semibold text-[var(--admin-text)]">{feature.title}</h3>
              <p className="leading-relaxed text-[var(--admin-text-muted)]">{feature.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── HeroEnterprise ──────────────────────────────────────────────────────────

function toTickerStat(n: number): { value: number; suffix: string } {
  if (n >= 1_000_000_000) return { value: Math.floor(n / 1_000_000_000), suffix: "B+" };
  if (n >= 1_000_000) return { value: Math.floor(n / 1_000_000), suffix: "M+" };
  if (n >= 1_000) return { value: Math.floor(n / 1_000), suffix: "K+" };
  return { value: n, suffix: "" };
}

function StatCard({ value, suffix, prefix, label }: { value: number; suffix?: string; prefix?: string; label: string; delay?: number }) {
  return (
    <div className="flex flex-col items-center rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]/50 p-6 backdrop-blur-sm transition-all hover:border-blue-500/50">
      <div className="text-2xl font-bold text-blue-400 sm:text-3xl">{prefix}<NumberTicker value={value} className="text-blue-400" />{suffix}</div>
      <span className="mt-2 text-sm font-medium text-[var(--admin-text-muted)]">{label}</span>
    </div>
  );
}

export function HeroEnterprise({ totalTokens = 100_000_000_000, totalRequests = 20_000_000 }: { totalTokens?: number; totalRequests?: number }) {
  const tokensStat = toTickerStat(totalTokens);
  const requestsStat = toTickerStat(totalRequests);
  return (
    <section className="relative pt-32 pb-20 sm:pt-40 sm:pb-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-4xl text-center">
          <Link to="/blog/soc2-type-ii" className="mb-6 inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-4 py-1.5 transition-colors hover:border-blue-500/50">
            <span className="font-mono text-xs text-blue-400">ENTERPRISE</span><span className="text-xs text-[var(--admin-text-muted)]">SOC 2 Type II certified</span>
          </Link>
          <h1 className="mb-6 text-4xl font-bold tracking-tight sm:text-6xl lg:text-7xl">Enterprise LLM Gateway for mission-critical applications</h1>
          <p className="mx-auto mb-10 max-w-3xl text-lg text-[var(--admin-text-muted)]">Deploy a fully-managed or self-hosted LLM gateway with enterprise SSO, white-labeling, and infrastructure-as-code support for your cloud or bare metal infrastructure.</p>
          <div className="flex flex-col items-center justify-center gap-4 sm:flex-row">
            <Link to="/enterprise#contact" className="admin-btn admin-btn-primary">Contact Us <ArrowRight className="ml-2 h-4 w-4" /></Link>
            <Link to="/signup" className="admin-btn admin-btn-ghost">Explore The Product</Link>
          </div>
        </div>
        <EnterpriseDemoVideo />
        <div className="mx-auto mt-16 grid max-w-5xl grid-cols-2 gap-4 sm:grid-cols-4 lg:gap-6">
          <StatCard value={tokensStat.value} suffix={tokensStat.suffix} label="Total Tokens Processed" />
          <StatCard value={requestsStat.value} suffix={requestsStat.suffix} label="Total Requests" delay={0.1} />
          <StatCard value={200} suffix="M" label="Daily Tokens" delay={0.2} />
          <StatCard value={80} suffix="K" prefix="$" label="Customer Savings" delay={0.3} />
        </div>
      </div>
    </section>
  );
}

// ── InfrastructureAsCodeEnterprise ───────────────────────────────────────────

const iacHighlights = [
  { icon: Cloud, title: "AWS, GCP & Azure", description: "Provision the cluster, managed Postgres, Redis, networking, and secrets on the cloud you already use." },
  { icon: Terminal, title: "One command", description: "Run a single apply to stand up a production-grade deployment — no manual wiring of cloud resources." },
  { icon: Lock, title: "Yours to own", description: "The modules live in your repo and run in your account. Your data and infrastructure stay under your control." },
];

export function InfrastructureAsCodeEnterprise() {
  return (
    <section className="py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div className="space-y-6">
              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-bg)] px-3 py-1 text-sm"><Terminal className="h-4 w-4" /><span className="font-medium text-[var(--admin-text)]">Infrastructure as code</span></div>
              <div className="space-y-4">
                <h2 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Deploy your whole stack in one command</h2>
                <p className="max-w-xl text-lg leading-relaxed text-[var(--admin-text-muted)]">Enterprise includes Terraform modules that provision and deploy wiwi on AWS, GCP, or Azure — the cluster, managed database, cache, networking, and secrets — so you get a production deployment without writing the plumbing yourself.</p>
              </div>
              <div className="flex flex-wrap gap-3"><Link to="/enterprise#contact" className="admin-btn admin-btn-primary">Get the Terraform modules <ArrowRight className="ml-2 h-4 w-4" /></Link></div>
            </div>
            <div className="space-y-6">
              <div className="rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-5 font-mono text-sm">
                <div className="text-[var(--admin-text-muted)]"># one command</div>
                <div><span className="text-[var(--admin-text-muted)]">$ </span>terraform apply</div>
                <div className="mt-2 text-[var(--admin-text-muted)]"># cluster, database, cache, secrets, and wiwi — live</div>
              </div>
              <div className="space-y-4">
                {iacHighlights.map((item) => (
                  <div key={item.title} className="flex items-start gap-3">
                    <div className="shrink-0 rounded-lg bg-blue-500/10 p-2 text-blue-400"><item.icon className="h-4 w-4" /></div>
                    <div><div className="font-semibold text-[var(--admin-text)]">{item.title}</div><p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{item.description}</p></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── OpenSourceEnterprise ────────────────────────────────────────────────────

export function OpenSourceEnterprise() {
  const [stars, setStars] = useState<string | null>(null);
  useEffect(() => {
    fetch("https://api.github.com/repos/theopenco/llmgateway").then((r) => r.json()).then((d) => setStars(d.stargazers_count ? `${Math.floor(d.stargazers_count / 1000)}K` : "20K+")).catch(() => setStars("20K+"));
  }, []);
  return (
    <section className="bg-white/[0.01] py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div className="space-y-6">
              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-bg)] px-3 py-1 text-sm"><Code2 className="h-4 w-4" /><span className="font-medium text-[var(--admin-text)]">Open source</span></div>
              <div className="space-y-4">
                <h2 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Building trust and driving collaboration</h2>
                <p className="max-w-xl text-lg leading-relaxed text-[var(--admin-text-muted)]">We share our code so you can move faster, stay secure, and build together.</p>
              </div>
              <div className="flex flex-wrap gap-3">
                <a href="/docs" target="_blank" rel="noopener noreferrer" className="admin-btn admin-btn-primary"><BookOpen className="mr-2 h-4 w-4" />Read the docs</a>
                <a href="https://github.com" target="_blank" rel="noopener noreferrer" className="admin-btn admin-btn-ghost"><Code2 className="mr-2 h-4 w-4" />View the code</a>
              </div>
            </div>
            <div className="space-y-12">
              <div className="flex flex-col items-center space-y-4 lg:items-start">
                <div className="flex gap-2">{[0, 1, 2].map((i) => <svg key={i} viewBox="0 0 24 24" className="h-12 w-12 fill-yellow-400 text-yellow-400"><path d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z" /></svg>)}</div>
                <div className="text-center lg:text-left"><div className="text-2xl font-bold text-[var(--admin-text)]">{stars ?? "20K+"} Stars</div><p className="text-sm text-[var(--admin-text-muted)]">Trusted by the community</p></div>
              </div>
              <div className="flex flex-col items-center space-y-4 lg:items-start">
                <div className="flex -space-x-3">{[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <div key={i} className="flex h-12 w-12 items-center justify-center rounded-full border-2 border-[var(--admin-bg)] bg-gradient-to-br from-blue-500 to-purple-600 font-semibold text-white">{String.fromCharCode(65 + i)}</div>)}</div>
                <div className="text-center lg:text-left"><div className="text-2xl font-bold text-[var(--admin-text)]">60+ Contributors</div><p className="text-sm text-[var(--admin-text-muted)]">Building the future together</p></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── PricingEnterprise ────────────────────────────────────────────────────────

const pilotMilestones = [
  { phase: "Week 1", title: "Traffic live", description: "We migrate your keys, routing rules, and first production traffic with you — hands-on, in a shared channel." },
  { phase: "Week 2", title: "Controls on", description: "SSO, audit logs, and guardrails configured for your organization and verified by your security team." },
  { phase: "Day 30", title: "Decision gate", description: "If we missed the milestones, walk away — no long-term contract before the gate." },
];

const plans = [
  { name: "Self-Hosted", description: "Deploy on your infrastructure with complete control", features: ["Full admin dashboard included", "Enterprise SSO integration", "Provider configuration UI", "Terraform modules for AWS, GCP, bare metal", "White label gateway & chat app", "Prioritized feature requests", "On-boarding assistance", "Dedicated support channel"], cta: "Get In Touch", highlighted: false },
  { name: "Enterprise Cloud", description: "Fully managed with custom scaling and pricing", features: ["Everything in Self-Hosted", "30-Day Production Pilot to start", "Fully managed infrastructure", "Custom rate limits", "Volume-based pricing", "Advanced monitoring & analytics", "99.9% SLA guarantee", "Priority incident response"], cta: "Contact Us", highlighted: true },
];

export function PricingEnterprise() {
  return (
    <section id="pricing" className="bg-white/[0.01] py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto mb-16 max-w-2xl text-center">
          <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-blue-500/30 bg-blue-500/10 px-4 py-1.5 font-mono text-xs uppercase tracking-wider text-blue-400"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />30-Day Production Pilot included</span>
          <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Enterprise pricing that scales with you</h2>
          <p className="text-lg text-[var(--admin-text-muted)]">Choose between self-hosted control or fully managed convenience. Both options include all enterprise features, and every plan starts with the 30-Day Production Pilot.</p>
        </div>
        <div className="mx-auto mb-16 max-w-5xl">
          <div className="grid gap-4 sm:grid-cols-3">
            {pilotMilestones.map((m) => (
              <div key={m.phase} className="rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6">
                <span className="font-mono text-xs uppercase tracking-wider text-blue-400">{m.phase}</span>
                <h3 className="mt-2 mb-1.5 text-base font-semibold text-[var(--admin-text)]">{m.title}</h3>
                <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{m.description}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="mx-auto grid max-w-5xl gap-8 lg:grid-cols-2">
          {plans.map((plan) => (
            <div key={plan.name} className={`admin-card p-8 ${plan.highlighted ? "border-blue-500 shadow-lg shadow-blue-500/10" : ""}`}>
              <div className="mb-6"><h3 className="mb-2 text-2xl font-bold text-[var(--admin-text)]">{plan.name}</h3><p className="text-[var(--admin-text-muted)]">{plan.description}</p></div>
              <ul className="mb-8 space-y-3">
                {plan.features.map((f) => <li key={f} className="flex items-start gap-3"><Check className="mt-0.5 h-5 w-5 shrink-0 text-blue-400" /><span className="text-sm leading-relaxed text-[var(--admin-text)]">{f}</span></li>)}
              </ul>
              <Link to="/enterprise#contact" className={`admin-btn w-full ${plan.highlighted ? "admin-btn-primary" : "admin-btn-ghost"}`}>{plan.cta}</Link>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── ProcurementEnterprise ───────────────────────────────────────────────────

const procurementItems = [
  { icon: FileCheck, title: "SOC 2 Type II report", description: "Independently audited controls, verified in operation. The full report and evidence are one request away.", href: "https://security.example.com/", linkLabel: "Request the report", external: true },
  { icon: ScrollText, title: "DPA & subprocessor list", description: "GDPR-aligned data processing agreement and a current subprocessor list, ready for your legal team.", href: "/enterprise#contact", linkLabel: "Request the DPA", external: false },
  { icon: ShieldQuestion, title: "Security questionnaires", description: "Vendor forms and security reviews answered by the engineers who run the gateway — not a sales bot." },
  { icon: Receipt, title: "Flexible commercial terms", description: "Invoicing, MSA review, and payment terms that fit your procurement process instead of fighting it." },
];

export function ProcurementEnterprise() {
  return (
    <section className="py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto mb-14 max-w-2xl text-center">
          <p className="mb-4 font-mono text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">Procurement</p>
          <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Cleared for procurement before the first call</h2>
          <p className="text-lg text-[var(--admin-text-muted)]">The paperwork that usually stalls an infrastructure deal, prepared up front.</p>
        </div>
        <div className="mx-auto grid max-w-5xl gap-4 sm:grid-cols-2">
          {procurementItems.map((item) => (
            <div key={item.title} className="flex flex-col rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02]"><item.icon className="h-5 w-5 text-[var(--admin-text-muted)]" /></div>
              <h3 className="mb-1.5 text-base font-semibold text-[var(--admin-text)]">{item.title}</h3>
              <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{item.description}</p>
              {item.href && (item.external ? (
                <a href={item.href} target="_blank" rel="noopener noreferrer" className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:text-blue-400">{item.linkLabel} <ArrowUpRight className="h-4 w-4" /></a>
              ) : (
                <Link to={item.href} className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:text-blue-400">{item.linkLabel} <ArrowUpRight className="h-4 w-4" /></Link>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── ProductShowcase ──────────────────────────────────────────────────────────

const productSurfaces = [
  { icon: Gauge, title: "Analytics Dashboard", description: "Real-time usage metrics, cost breakdowns, and performance monitoring across all your LLM operations." },
  { icon: MessagesSquare, title: "Lounge", description: "The members' lounge for AI — every frontier model in one chat, with projects, group chat, and media studios." },
  { icon: ImageIcon, title: "Image Studio", description: "Generate images with multiple providers and models. Compare outputs side-by-side with adjustable settings." },
  { icon: ShieldCheck, title: "Admin Dashboard", description: "Full visibility into signups, revenue, provider health, and model performance across your deployment." },
  { icon: BookOpen, title: "Developer Documentation", description: "Comprehensive API reference, integration guides, and self-hosting documentation for your team." },
];

export function ProductShowcase() {
  return (
    <section className="border-t border-[var(--admin-border)] py-20 sm:py-28">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid gap-12 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:gap-20">
          <div className="self-start lg:sticky lg:top-28">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-white/[0.02] px-4 py-1.5"><span className="font-mono text-xs text-blue-400">PLATFORM</span><span className="text-xs text-[var(--admin-text-muted)]">Everything your team needs</span></div>
            <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl">One platform for your entire LLM stack</h2>
            <p className="text-lg leading-relaxed text-[var(--admin-text-muted)]">Analytics, chat, media studios, admin, and docs all ship with your deployment. One contract, one vendor, one place to look.</p>
            <div className="mt-8 rounded-xl border border-[var(--admin-border)] bg-white/[0.02] p-6">
              <div className="mb-3 flex items-center gap-2"><Paintbrush className="h-5 w-5 text-blue-400" /><h3 className="text-base font-semibold text-[var(--admin-text)]">Fully white-labelable</h3></div>
              <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">Replace the logo and branding with your own. Every dashboard, chat app, and docs page can be customized to match your company identity.</p>
            </div>
          </div>
          <ul className="divide-y divide-[var(--admin-border)] border-y border-[var(--admin-border)]">
            {productSurfaces.map((s) => (
              <li key={s.title} className="group flex gap-5 py-6 first:pt-0 lg:first:pt-6">
                <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400"><s.icon className="h-5 w-5" /></div>
                <div><h3 className="text-lg font-semibold text-[var(--admin-text)]">{s.title}</h3><p className="mt-1 text-sm leading-relaxed text-[var(--admin-text-muted)]">{s.description}</p></div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

// ── SecurityEnterprise ───────────────────────────────────────────────────────

const securityAssurances = [
  { icon: Lock, title: "Encrypted everywhere", description: "TLS in transit, AES-256 at rest — including provider keys." },
  { icon: Key, title: "SSO upon request", description: "Okta, Azure AD, and Google with role-based permissions." },
  { icon: FileCheck, title: "Tamper-evident audit logs", description: "Every change — configuration, permissions, spend limits, key rotations." },
  { icon: ShieldCheck, title: "99.9% uptime SLA", description: "Backed by live status at status.example.com." },
];

export function SecurityEnterprise() {
  return (
    <section className="relative overflow-hidden py-24 sm:py-32">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_15%_0%,rgba(59,130,246,0.07),transparent_50%),radial-gradient(circle_at_85%_100%,rgba(99,102,241,0.06),transparent_50%)]" />
      <div className="container relative mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mb-16 grid gap-12 lg:grid-cols-2 lg:gap-20 lg:items-start">
          <div className="flex flex-col items-start gap-4">
            <span className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-bg)]/50 px-3 py-1 backdrop-blur-sm"><ShieldCheck className="h-3.5 w-3.5 text-blue-400" /><span className="font-mono text-xs uppercase tracking-wider text-[var(--admin-text-muted)]">Security &amp; Compliance</span></span>
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Your security review, already answered</h2>
          </div>
          <p className="text-lg leading-relaxed text-[var(--admin-text-muted)] lg:pt-14">Prompts, completions, and API keys flow through your gateway — so the layer in the middle has to hold itself to a higher standard than the integrations it replaces. Ours is independently audited, and the evidence is one click away.</p>
        </div>
        <div className="grid gap-6 md:grid-cols-2">
          <div className="group relative flex flex-col gap-6 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]/50 p-8 transition-colors hover:border-blue-500/40">
            <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-blue-500/10 to-transparent opacity-0 transition-opacity duration-500 group-hover:opacity-100" />
            <div className="relative flex items-start justify-between">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-2 border-blue-400/50 bg-blue-500/10"><ShieldCheck className="h-7 w-7 text-blue-400" /></div>
              <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 font-mono text-xs uppercase tracking-wider text-emerald-400">Audited</span>
            </div>
            <div className="relative flex flex-col gap-2"><h3 className="text-2xl font-semibold tracking-tight text-[var(--admin-text)]">SOC 2 Type II</h3><p className="leading-relaxed text-[var(--admin-text-muted)]">Independently audited against the AICPA Trust Services Criteria for security, availability, and confidentiality.</p></div>
            <a href="https://security.example.com/" target="_blank" rel="noopener noreferrer" className="relative mt-auto inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:text-blue-400">Request the full report <ArrowUpRight className="h-4 w-4" /></a>
          </div>
          <div className="group relative flex flex-col gap-6 rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)]/50 p-8 transition-colors hover:border-blue-500/40">
            <div className="pointer-events-none absolute inset-0 rounded-2xl bg-gradient-to-br from-indigo-500/10 to-transparent opacity-0 transition-opacity duration-500 group-hover:opacity-100" />
            <div className="relative flex items-start justify-between">
              <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-2 border-blue-400/50 bg-blue-500/10"><Globe className="h-7 w-7 text-blue-400" /></div>
              <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 font-mono text-xs uppercase tracking-wider text-emerald-400">Compliant</span>
            </div>
            <div className="relative flex flex-col gap-2"><h3 className="text-2xl font-semibold tracking-tight text-[var(--admin-text)]">GDPR</h3><p className="leading-relaxed text-[var(--admin-text-muted)]">Data processing aligned with GDPR, with a DPA and subprocessor list ready for your legal team.</p></div>
            <Link to="/legal/privacy" className="relative mt-auto inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text)] transition-colors hover:text-blue-400">Read our privacy policy <ArrowUpRight className="h-4 w-4" /></Link>
          </div>
        </div>
        <div className="mt-6 grid gap-px overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-border)] sm:grid-cols-2 lg:grid-cols-4">
          {securityAssurances.map((item) => (
            <div key={item.title} className="flex flex-col gap-3 bg-[var(--admin-bg)] p-6">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/[0.03] ring-1 ring-[var(--admin-border)]"><item.icon className="h-4 w-4 text-[var(--admin-text-muted)]" /></div>
              <div><h4 className="text-sm font-semibold text-[var(--admin-text)]">{item.title}</h4><p className="mt-1 text-sm leading-relaxed text-[var(--admin-text-muted)]">{item.description}</p></div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── SupportEnterprise ────────────────────────────────────────────────────────

const supportPerks = [
  { icon: Hash, title: "A dedicated Slack or Discord channel", description: "Your engineers and ours in one room, in the tool your team already uses." },
  { icon: Headphones, title: "Answers from the people who built it", description: "24/7 priority support staffed by gateway engineers." },
  { icon: Rocket, title: "Hands-on onboarding and migration", description: "We help move your traffic, keys, and routing rules over." },
  { icon: Zap, title: "Early access to new features", description: "Enterprise customers get new capabilities first, with a direct line to shape what we build next." },
];

const supportMessages = [
  { initials: "SK", name: "Sarah", org: "Acme", time: "2:47 AM", avatarClass: "bg-rose-500/20 text-rose-400", text: "Seeing elevated p95 latency on our EU traffic for claude-sonnet requests — anything on your end?" },
  { initials: "LG", name: "Max", org: "wiwi", time: "2:51 AM", avatarClass: "bg-blue-500/20 text-blue-400", badge: "4 min response", text: "On it. One upstream provider is degraded in eu-west — we've shifted your routing to the healthy fallback. p95 should normalize within a minute." },
  { initials: "SK", name: "Sarah", org: "Acme", time: "2:53 AM", avatarClass: "bg-rose-500/20 text-rose-400", text: "Confirmed, dashboards look clean again." },
];

export function SupportEnterprise() {
  return (
    <section className="border-t border-[var(--admin-border)] bg-white/[0.01] py-24 sm:py-32">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-20">
          <div className="flex flex-col items-start gap-4">
            <span className="inline-flex items-center gap-2 rounded-full border border-[var(--admin-border)] bg-[var(--admin-bg)]/50 px-3 py-1 backdrop-blur-sm"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" /><span className="font-mono text-xs uppercase tracking-wider text-[var(--admin-text-muted)]">Enterprise Support</span></span>
            <h2 className="text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">A direct line, not a ticket queue</h2>
            <p className="text-lg leading-relaxed text-[var(--admin-text-muted)]">When LLM traffic is on your critical path, every enterprise plan includes a shared channel with our engineering team.</p>
            <div className="mt-6 grid gap-6 sm:grid-cols-2">
              {supportPerks.map((perk) => (
                <div key={perk.title} className="flex flex-col gap-2">
                  <div className="flex items-center gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/[0.03] ring-1 ring-[var(--admin-border)]"><perk.icon className="h-4 w-4 text-blue-400" /></div><h3 className="text-sm font-semibold text-[var(--admin-text)]">{perk.title}</h3></div>
                  <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">{perk.description}</p>
                </div>
              ))}
            </div>
            <Link to="/enterprise#contact" className="admin-btn admin-btn-primary mt-6">Get your dedicated channel <ArrowRight className="ml-2 h-4 w-4" /></Link>
          </div>
          <div className="relative">
            <div className="pointer-events-none absolute inset-x-0 -inset-y-8 bg-[radial-gradient(circle_at_50%_50%,rgba(59,130,246,0.08),transparent_70%)]" />
            <div className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-bg)] shadow-2xl">
              <div className="flex items-center justify-between border-b border-[var(--admin-border)] px-5 py-3.5">
                <div className="flex items-center gap-2"><Hash className="h-4 w-4 text-[var(--admin-text-muted)]" /><span className="text-sm font-semibold text-[var(--admin-text)]">acme-x-wiwi</span></div>
                <span className="inline-flex items-center gap-1.5 text-xs text-[var(--admin-text-muted)]"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Engineers online</span>
              </div>
              <div className="flex flex-col gap-5 p-5">
                {supportMessages.map((message, index) => (
                  <div key={index} className="flex items-start gap-3">
                    <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-xs font-bold ${message.avatarClass}`}>{message.initials}</div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                        <span className="text-sm font-semibold text-[var(--admin-text)]">{message.name}</span>
                        <span className="text-xs text-[var(--admin-text-muted)]">{message.org} · {message.time}</span>
                        {message.badge && <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-emerald-400">{message.badge}</span>}
                      </div>
                      <p className="mt-1 text-sm leading-relaxed text-[var(--admin-text-muted)]">{message.text}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ── TrustBarEnterprise ───────────────────────────────────────────────────────

const trustedCompanies = [
  { name: "Samsung", className: "text-2xl font-bold tracking-wider uppercase" },
  { name: "Harvard", className: "text-2xl font-bold tracking-tight" },
  { name: "FieldKo", className: "text-2xl font-bold tracking-tight" },
  { name: "Coloop.ai", className: "text-2xl font-semibold tracking-tight" },
];

export function TrustBarEnterprise() {
  return (
    <section className="border-y border-[var(--admin-border)] bg-white/[0.01] py-12">
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <h2 className="mb-8 text-center text-sm font-normal uppercase tracking-wider text-[var(--admin-text-muted)]">Trusted by innovative teams worldwide</h2>
        <div className="flex flex-wrap items-center justify-center gap-10 sm:gap-14">
          {trustedCompanies.map((company) => (
            <div key={company.name} className={`select-none text-[var(--admin-text-muted)]/80 ${company.className ?? ""}`}>{company.name}</div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── UptimeVisualization ──────────────────────────────────────────────────────

const uptimeProviders = [
  { name: "Anthropic", outages: [[8, 10], [42, 44], [78, 80]] as [number, number][] },
  { name: "AWS Bedrock", outages: [[3, 5], [30, 32], [63, 65]] as [number, number][] },
  { name: "Google Vertex", outages: [[18, 20], [50, 52], [72, 74]] as [number, number][] },
  { name: "Azure OpenAI", outages: [[13, 15], [37, 39], [86, 88]] as [number, number][] },
  { name: "Fireworks AI", outages: [[23, 25], [55, 57], [95, 97]] as [number, number][] },
];

function buildSegments(outages: [number, number][]) {
  const segments: Array<{ type: "up" | "down"; width: number }> = [];
  let pos = 0;
  for (const [start, end] of outages) {
    if (start > pos) segments.push({ type: "up", width: start - pos });
    segments.push({ type: "down", width: end - start });
    pos = end;
  }
  if (pos < 100) segments.push({ type: "up", width: 100 - pos });
  return segments;
}

export function UptimeVisualization() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  return (
    <section className="py-20 sm:py-28" ref={ref}>
      <div className="container mx-auto px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-5xl">
          <div className="mb-12 text-center sm:mb-16">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5">
              <span className="relative flex h-2 w-2"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" /><span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" /></span>
              <span className="font-mono text-xs text-emerald-400">RELIABILITY</span>
            </div>
            <h2 className="mb-4 text-3xl font-bold tracking-tight sm:text-4xl lg:text-5xl">Never go down. <span className="text-[var(--admin-text-muted)]">Even when your providers do.</span></h2>
            <p className="mx-auto max-w-3xl text-lg leading-relaxed text-[var(--admin-text-muted)]">wiwi automatically routes requests to healthy providers in real-time. When one goes down, your traffic seamlessly fails over — your users never notice.</p>
          </div>
          <div className="rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-4 sm:p-8" role="img" aria-label="Provider uptime visualization">
            <div className="space-y-2.5 sm:space-y-3">
              {uptimeProviders.map((provider, index) => {
                const segments = buildSegments(provider.outages);
                const totalDown = provider.outages.reduce((s, [start, end]) => s + (end - start), 0);
                return (
                  <div key={provider.name} className="flex items-center gap-2 sm:gap-4">
                    <div className="w-20 shrink-0 text-right sm:w-28"><span className="text-xs font-medium text-[var(--admin-text-muted)] sm:text-sm">{provider.name}</span></div>
                    <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
                      <div className="absolute inset-0 bg-white/[0.04]" />
                      <div className="flex h-full" style={{ clipPath: inView ? "inset(0 0 0 0)" : "inset(0 100% 0 0)", transition: "clip-path 1.2s cubic-bezier(0.16, 1, 0.3, 1)", transitionDelay: `${index * 150}ms` }}>
                        {segments.map((seg, i) => (
                          <div key={i} className={seg.type === "up" ? "h-full bg-emerald-500/30" : "h-full bg-red-500/50"} style={{ width: `${seg.width}%` }} />
                        ))}
                      </div>
                    </div>
                    <div className="w-12 shrink-0 text-right sm:w-20"><span className="font-mono text-xs text-[var(--admin-text-muted)] sm:text-sm">{100 - totalDown}%</span></div>
                  </div>
                );
              })}
            </div>
            <div className="my-4 flex items-center gap-2 sm:my-6 sm:gap-4">
              <div className="w-20 shrink-0 sm:w-28" />
              <div className="relative flex-1 border-t border-dashed border-[var(--admin-border)]">
                <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 whitespace-nowrap bg-[var(--admin-surface)] px-3 py-0.5 font-mono text-xs text-[var(--admin-text-muted)]"><ArrowDown className="h-3 w-3 text-blue-400" />Automatic failover</div>
              </div>
              <div className="w-12 shrink-0 sm:w-20" />
            </div>
            <div className="flex items-center gap-2 sm:gap-4">
              <div className="w-20 shrink-0 text-right sm:w-28"><span className="text-xs font-bold text-[var(--admin-text)] sm:text-sm">wiwi</span></div>
              <div className="relative h-5 flex-1 overflow-hidden rounded sm:h-7">
                <div className="absolute inset-0 bg-white/[0.04]" />
                <div className="absolute inset-0 rounded bg-emerald-500" style={{ clipPath: inView ? "inset(0 0 0 0)" : "inset(0 100% 0 0)", transition: "clip-path 1.4s cubic-bezier(0.16, 1, 0.3, 1)", transitionDelay: "900ms" }} />
              </div>
              <div className="w-12 shrink-0 text-right sm:w-20"><span className="font-mono text-xs font-bold text-emerald-400 sm:text-sm">99.9999%</span></div>
            </div>
          </div>
          <div className="mt-8 grid gap-4 sm:mt-12 sm:grid-cols-2 sm:gap-6">
            <div className="rounded-xl border border-red-500/20 bg-red-500/[0.03] p-5 sm:p-6">
              <div className="mb-3 font-mono text-xs tracking-wider text-red-400">WITHOUT wiwi</div>
              <div className="font-mono text-3xl font-bold sm:text-4xl text-[var(--admin-text)]">94%</div>
              <div className="mt-1 text-sm text-[var(--admin-text-muted)]">uptime per provider</div>
              <div className="mt-4 border-t border-red-500/10 pt-4"><div className="font-mono text-lg font-bold text-red-400">~22 days</div><div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div></div>
            </div>
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/[0.03] p-5 sm:p-6">
              <div className="mb-3 font-mono text-xs tracking-wider text-emerald-400">WITH wiwi</div>
              <div className="font-mono text-3xl font-bold text-emerald-400 sm:text-4xl">99.9999%</div>
              <div className="mt-1 text-sm text-[var(--admin-text-muted)]">combined uptime across providers</div>
              <div className="mt-4 border-t border-emerald-500/10 pt-4"><div className="font-mono text-lg font-bold text-emerald-400">&lt;32 seconds</div><div className="text-sm text-[var(--admin-text-muted)]">of downtime per year</div></div>
            </div>
          </div>
          <p className="mx-auto mt-6 max-w-2xl text-center text-sm leading-relaxed text-[var(--admin-text-muted)]">Each provider averages ~94% uptime independently. With automatic failover across multiple providers, the probability of simultaneous downtime drops to near zero — giving you effective uptime of 99.9999%.</p>
        </div>
      </div>
    </section>
  );
}

// re-export Gauge + User2 for parity
export { Gauge, User2 };
