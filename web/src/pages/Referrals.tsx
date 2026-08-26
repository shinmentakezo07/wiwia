// Referrals — referral program with feature highlights. Adapted from the
// llmgateway.io referrals page with inlined data, in the dark design system.

import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  Code2,
  DollarSign,
  Gift,
  Globe,
  Image as ImageIcon,
  KeyRound,
  Network,
  RefreshCw,
  Share2,
  Shield,
  Sparkles,
  TrendingUp,
  Users,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Badge, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const SELLING_POINTS: { icon: LucideIcon; title: string; description: string; href: string; accent: string; accentBg: string; external?: boolean }[] = [
  { icon: Network, title: "200+ Models, One API", description: "Access OpenAI, Anthropic, Google, Meta, Mistral, and 40+ providers through a single OpenAI-compatible endpoint. Zero code changes to switch providers.", href: "/features/unified-api-interface", accent: "text-violet-400", accentBg: "bg-violet-500/10" },
  { icon: RefreshCw, title: "Automatic Failover", description: "When a provider goes down or rate-limits you, requests automatically route to the next best provider. Your users never notice the difference.", href: "/features/multi-provider-support", accent: "text-emerald-400", accentBg: "bg-emerald-500/10" },
  { icon: ImageIcon, title: "Nano Banana Simulator", description: "Up to 20% off Google Gemini 3 Pro image generation. Use the cost simulator to see exactly how much you save at any volume.", href: "/nano-banana-simulator", accent: "text-amber-400", accentBg: "bg-amber-500/10" },
  { icon: DollarSign, title: "5% Platform Fee", description: "Lower than competitors. OpenRouter charges 5.5%. Bring your own keys and pay zero platform fees.", href: "/pricing", accent: "text-green-400", accentBg: "bg-green-500/10" },
  { icon: Code2, title: "Dev Plans for AI Coding", description: "Fixed-price plans from $29/mo for Claude Code, Cursor, and Windsurf. Get 3x your subscription in monthly usage with all models included.", href: "/code", accent: "text-blue-400", accentBg: "bg-blue-500/10", external: true },
  { icon: Shield, title: "Guardrails & Safety", description: "Built-in prompt injection protection, PII detection, secrets scanning, and custom content rules. Compliance without the overhead.", href: "/features/guardrails", accent: "text-rose-400", accentBg: "bg-rose-500/10" },
  { icon: Zap, title: "Prompt Caching", description: "Automatic response caching cuts costs and latency on repeated queries. Toggle it per-project from the dashboard.", href: "/features/performance-monitoring", accent: "text-orange-400", accentBg: "bg-orange-500/10" },
  { icon: Globe, title: "Self-Host for Free", description: "Open source under AGPLv3. Deploy on your own infrastructure for full data control, or use the managed cloud for instant setup.", href: "/features/self-hosted-or-cloud", accent: "text-cyan-400", accentBg: "bg-cyan-500/10" },
  { icon: KeyRound, title: "Bring Your Own Keys", description: "Use your existing provider API keys with zero platform fee. Get unified analytics, failover, and guardrails on top of your own accounts.", href: "/pricing", accent: "text-purple-400", accentBg: "bg-purple-500/10" },
];

const COMPARISON: { feature: string; us: string; them: string }[] = [
  { feature: "Platform Fee", us: "5%", them: "5.5%" },
  { feature: "BYOK Fee", us: "Free", them: "1M free reqs/mo, then 5%" },
  { feature: "Auto Failover", us: "Built-in", them: "Yes" },
  { feature: "Analytics", us: "Request-level insights", them: "Logs + export" },
  { feature: "Self-Hosting", us: "Free (AGPLv3)", them: "Not available" },
  { feature: "Guardrails", us: "PII, injection, secrets", them: "Enterprise" },
  { feature: "Dev Plans (Coding)", us: "From $29/mo", them: "Not available" },
  { feature: "Image Gen Discounts", us: "Up to 20% off", them: "No discounts" },
];

const REFERRAL_STEPS: { step: number; icon: LucideIcon; title: string; description: string }[] = [
  { step: 1, icon: Sparkles, title: "Unlock referrals", description: "Top up $100 in credits to become eligible and access your unique referral link from the dashboard." },
  { step: 2, icon: Share2, title: "Share your link", description: "Send your referral link to teams who could benefit. Share any feature page above to make the case." },
  { step: 3, icon: TrendingUp, title: "Earn continuously", description: "Automatically earn 1% of their LLM spending as credits, deposited directly to your account balance." },
];

const PROGRAM_DETAILS: { title: string; description: string }[] = [
  { title: "Post-discount earnings", description: "Commission is calculated on LLM usage after any discounts are applied." },
  { title: "Direct credit deposits", description: "Credits are automatically added to your balance. No manual claims needed." },
  { title: "Use for any LLM service", description: "Referral credits work for any model or provider. Cannot be withdrawn." },
  { title: "Unlimited referrals", description: "No cap on how many users you refer or how much you can earn." },
];

export function ReferralsPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="relative overflow-hidden rounded-2xl border border-[var(--admin-border)] text-center" style={{ background: "linear-gradient(to bottom, rgba(59,130,246,0.04), transparent)" }}>
        <div className="p-8 sm:p-12">
          <Badge tone="blue">
            <Gift size={12} className="mr-1.5" />
            Referral Program
          </Badge>
          <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
            Share the gateway,{" "}
            <span className="bg-gradient-to-r from-blue-400 to-blue-400/60 bg-clip-text text-transparent">
              earn credits
            </span>
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
            Earn{" "}
            <span className="font-semibold text-[var(--admin-text)]">1% of all LLM spending</span>{" "}
            from every team you refer. Below is everything that makes the gateway worth
            recommending.
          </p>
          <div className="mt-6 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
            >
              Get started
              <ArrowRight size={16} />
            </Link>
            <a
              href="#why-switch"
              className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              See why teams switch
            </a>
          </div>
          <div className="mt-8 grid gap-4 sm:grid-cols-3">
            {[
              { value: "1%", label: "Of their LLM spend" },
              { value: "∞", label: "Unlimited referrals" },
              { value: "Auto", label: "Credits added instantly" },
            ].map((s) => (
              <Card key={s.label} className="p-6 text-center">
                <div className="font-mono text-[24px] font-bold text-blue-400" style={{ fontFamily: MONO }}>{s.value}</div>
                <div className="mt-1 text-[13px] text-[var(--admin-text-muted)]">{s.label}</div>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* ── why switch ── */}
      <section id="why-switch" className="scroll-mt-20">
        <div className="mb-6 space-y-2 text-center">
          <Badge tone="gray">Why Teams Switch</Badge>
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Everything you need to make the case
          </h2>
          <p className="mx-auto max-w-xl text-[14px] text-[var(--admin-text-muted)]">
            These are the features that convince teams to switch. Each one links to a
            detailed page you can share.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SELLING_POINTS.map((point) => {
            const Icon = point.icon;
            const content = (
              <Card className="group relative h-full overflow-hidden transition-colors hover:border-[var(--admin-border-hover)]">
                <div className="relative flex h-full flex-col p-5">
                  <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-lg ${point.accentBg}`}>
                    <Icon className={`h-5 w-5 ${point.accent}`} />
                  </div>
                  <h3 className="mb-2 text-[15px] font-semibold tracking-tight text-[var(--admin-text)]">{point.title}</h3>
                  <p className="mb-4 flex-grow text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{point.description}</p>
                  <div className={`inline-flex items-center text-[13px] font-medium ${point.accent}`}>
                    Learn more
                    <ArrowRight size={14} className="ml-1 transition-transform group-hover:translate-x-0.5" />
                  </div>
                </div>
              </Card>
            );
            if (point.external) {
              return (
                <a key={point.title} href={point.href} target="_blank" rel="noopener noreferrer" className="h-full">
                  {content}
                </a>
              );
            }
            return (
              <Link key={point.title} to={point.href} className="h-full">
                {content}
              </Link>
            );
          })}
        </div>
      </section>

      {/* ── comparison ── */}
      <section>
        <div className="mb-6 text-center">
          <Badge tone="gray">Competitive Edge</Badge>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">How we compare</h2>
        </div>
        <div className="overflow-hidden rounded-xl border border-[var(--admin-border)]">
          <table className="w-full text-left text-[13px]">
            <thead className="bg-white/[0.02]">
              <tr>
                <th className="px-5 py-3 font-medium text-[var(--admin-text-muted)]">Feature</th>
                <th className="px-5 py-3 text-center font-semibold text-[var(--admin-text)]">Gateway</th>
                <th className="px-5 py-3 text-center font-medium text-[var(--admin-text-muted)]">OpenRouter</th>
              </tr>
            </thead>
            <tbody>
              {COMPARISON.map((row) => (
                <tr key={row.feature} className="border-t border-[var(--admin-border)]">
                  <td className="px-5 py-3 font-medium text-[var(--admin-text)]">{row.feature}</td>
                  <td className="px-5 py-3 text-center font-medium text-blue-400">{row.us}</td>
                  <td className="px-5 py-3 text-center text-[var(--admin-text-muted)]">{row.them}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-5 text-center">
          <Link to="/compare/open-router" className="inline-flex items-center gap-1.5 text-[14px] font-medium text-[var(--admin-text)] hover:text-blue-400">
            See full comparison
            <ArrowRight size={14} />
          </Link>
        </div>
      </section>

      {/* ── how referrals work ── */}
      <section>
        <div className="mb-6 text-center">
          <Badge tone="gray">3 Simple Steps</Badge>
          <h2 className="mt-2 text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            How referrals work
          </h2>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          {REFERRAL_STEPS.map((item) => {
            const Icon = item.icon;
            return (
              <Card key={item.step} className="relative overflow-hidden p-5 transition-colors hover:border-[var(--admin-border-hover)]">
                <div className="space-y-4">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 text-blue-400">
                    <Icon className="h-5 w-5" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-500/10 text-[12px] font-bold text-blue-400">
                        {item.step}
                      </span>
                      <h3 className="text-[15px] font-semibold text-[var(--admin-text)]">{item.title}</h3>
                    </div>
                    <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{item.description}</p>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── program details ── */}
      <section>
        <Card className="p-6 md:p-8">
          <div className="space-y-6">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/10">
                <Gift className="h-5 w-5 text-blue-400" />
              </div>
              <h3 className="text-[20px] font-bold text-[var(--admin-text)]">Program details</h3>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {PROGRAM_DETAILS.map((detail) => (
                <div key={detail.title} className="flex gap-3">
                  <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
                    <Check size={12} className="text-blue-400" />
                  </div>
                  <div className="space-y-1">
                    <p className="font-medium text-[var(--admin-text)]">{detail.title}</p>
                    <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{detail.description}</p>
                  </div>
                </div>
              ))}
            </div>
            <div className="rounded-lg border border-[var(--admin-border)] bg-white/[0.02] p-5">
              <div className="flex items-start gap-3">
                <Users className="mt-0.5 h-5 w-5 text-blue-400" />
                <div className="space-y-1">
                  <p className="font-medium text-[var(--admin-text)]">Eligibility</p>
                  <p className="text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                    Top up $100 in credits to unlock. Available in your organization dashboard
                    under Referrals.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </Card>
      </section>

      {/* ── final CTA ── */}
      <section className="text-center">
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Ready to start earning?
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          Sign up, unlock the referral program, and share the features above with your
          network. Every team that switches earns you credits.
        </p>
        <div className="mt-5 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Link
            to="/signup"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Get started
            <ArrowRight size={16} />
          </Link>
          <Link
            to="/pricing"
            className="inline-flex items-center justify-center gap-2 rounded-[10px] border border-white/[0.08] bg-white/[0.02] px-6 py-3 text-[14px] font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
          >
            View pricing
          </Link>
        </div>
      </section>
    </div>
  );
}
