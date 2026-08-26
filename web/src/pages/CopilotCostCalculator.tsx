// Copilot Cost Calculator — GitHub Copilot usage-based AI Credits calculator.
// Adapted from the llmgateway.io page with inlined FAQ data, in the dark design system.

import { Link } from "react-router-dom";
import { ChevronDown, Gauge, SlidersHorizontal, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const STEPS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: Users,
    title: "Describe your team",
    description:
      "Set your headcount, Copilot plan, and how much each developer actually uses chat and agent mode per day. Presets cover light chat through fully agentic workflows.",
  },
  {
    icon: SlidersHorizontal,
    title: "Tune the assumptions",
    description:
      "Pick the model mix, adjust the prompt cache hit rate, and toggle bring-your-own-keys. Every constant in the math is documented and adjustable.",
  },
  {
    icon: Gauge,
    title: "Compare the two bills",
    description:
      "See the estimated monthly Copilot AI Credits bill next to the same workload at pass-through token prices with caching — and what a hard budget cap means for the worst case.",
  },
];

const ASSUMPTIONS = [
  "A chat session is a ~5-turn conversation totaling 30,000 input and 4,000 output tokens — history is resent every turn, which is why sessions cost more than single prompts.",
  "An agent task is a multi-step run totaling 150,000 input and 8,000 output tokens, dominated by repeatedly resent repo context.",
  "A month is 21 working days.",
  "Both sides are priced from the same token volumes at the same per-million-token rates (premium $5/$25, efficient $0.25/$2, balanced in between), so the comparison isolates structure — seats and included credits versus caching and the platform fee.",
  "Cached input tokens are billed at roughly 10% of the input rate. The cache hit rate slider controls how much of your input traffic is cached; 60% is a conservative default for coding tools.",
  "Copilot's included credits ($15 Pro, $70 Pro+, $200 Max) offset usage; Business and Enterprise allowances vary by agreement, so they're an editable field rather than a guess.",
];

const FAQ: { question: string; answer: string }[] = [
  {
    question: "How does GitHub Copilot billing work in 2026?",
    answer:
      "Since June 1, 2026, GitHub Copilot bills chat, agent mode, code review, and CLI usage in AI Credits (1 credit = $0.01) on top of the seat price. Seats cost $10 (Pro), $39 (Pro+), $100 (Max), $19/user (Business), or $39/user (Enterprise). Pro includes $15 of monthly credits, Pro+ $70, and Max $200; usage beyond that bills per token with no ceiling unless you set a manual budget. Inline completions remain flat-fee.",
  },
  {
    question: "How does this calculator estimate Copilot costs?",
    answer:
      "It models a chat session as a ~5-turn conversation totaling about 30,000 input and 4,000 output tokens (history is resent every turn), and an agent task as roughly 150,000 input and 8,000 output tokens. Both Copilot AI Credits and the gateway are priced from the same token volumes at the same per-million-token rates, so the comparison isolates the structural differences: seat fees and included credits versus prompt caching and a flat platform fee.",
  },
  {
    question: "Why is the gateway estimate usually lower?",
    answer:
      "Three reasons: there's no per-seat fee for API usage, provider token rates pass through with zero markup (a flat 5% fee on credits, or 0% with your own provider keys), and prompt caching bills the repeated context that coding tools resend on every request at roughly 10% of the normal input rate. Agentic workloads resend a lot of context, so caching does most of the work.",
  },
  {
    question: "Can I set a hard cap on what my team spends?",
    answer:
      "Yes. The gateway enforces budgets with hard limits per organization, project, and API key — requests stop at the cap instead of billing past it. Copilot's spending budgets exist in the billing dashboard but are off by default.",
  },
  {
    question: "What if I want a flat monthly price per developer?",
    answer:
      "DevPass plans give each developer a flat monthly allowance ($29–$179/month) usable across coding agents like DevPass Code, Claude Code, and Cline, with roughly 3x the plan price in monthly usage value. It's the predictable-seat model Copilot used to be, but with model choice.",
  },
  {
    question: "How accurate are these estimates?",
    answer:
      "They're planning estimates, not invoices. Real costs depend on your models' exact rates, conversation lengths, agent context sizes, and cache hit rates — all of which vary by team. The assumptions are documented on this page and every knob is adjustable, so you can match the math to your own usage before you commit to anything.",
  },
  {
    question: "Do I have to stop using GitHub Copilot entirely?",
    answer:
      "No. Inline completions weren't moved to usage billing and remain excellent. A common setup keeps Copilot Free or a $10 Pro seat for completions and routes chat and agent workloads through the gateway, where they're cached, capped, and billed at pass-through rates.",
  },
  {
    question: "Is the Copilot cost calculator free to use?",
    answer:
      "Yes — free, no signup, and everything runs in your browser. When you're ready to test real traffic, a gateway account is free to create and works with any OpenAI-compatible tool.",
  },
];

export function CopilotCostCalculatorPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-10 pb-16">
      {/* ── hero ── */}
      <section className="text-center">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)] sm:text-4xl">
          GitHub Copilot{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            Cost Calculator
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Free calculator for GitHub Copilot&apos;s usage-based AI Credits billing.
          Estimate your team&apos;s monthly Copilot bill and compare the same workload at
          pass-through token prices with caching and hard budget caps.
        </p>
      </section>

      {/* ── calculator placeholder ── */}
      <section>
        <Card className="p-8 text-center">
          <Gauge className="mx-auto h-10 w-10 text-blue-400" />
          <h2 className="mt-4 text-[18px] font-semibold text-[var(--admin-text)]">
            Interactive calculator
          </h2>
          <p className="mt-2 text-[14px] text-[var(--admin-text-muted)]">
            Enter your team size, Copilot plan, and daily usage to estimate costs and
            compare against pass-through gateway pricing with prompt caching.
          </p>
          <Link
            to="/signup"
            className="mt-5 inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-6 py-3 text-[14px] font-medium text-white shadow-lg shadow-brand-600/20 transition-[filter] hover:brightness-110"
          >
            Start free
          </Link>
        </Card>
      </section>

      {/* ── how it works ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            How the Copilot cost calculator works
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
            Estimate what GitHub Copilot&apos;s usage-based AI Credits cost your team each
            month, then compare the same workload routed through the gateway in three
            steps.
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <Card key={step.title} className="p-5">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 text-blue-400">
                    <Icon className="h-5 w-5" />
                  </div>
                  <span className="font-mono text-[13px] font-semibold text-[var(--admin-text-muted)]" style={{ fontFamily: MONO }}>
                    Step {index + 1}
                  </span>
                </div>
                <h3 className="mt-4 text-[15px] font-semibold text-[var(--admin-text)]">{step.title}</h3>
                <p className="mt-2 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{step.description}</p>
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── explainer ── */}
      <section>
        <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
          Understanding GitHub Copilot&apos;s 2026 pricing
        </h2>
        <div className="mt-5 space-y-4 text-[14px] leading-relaxed text-[var(--admin-text-muted)]">
          <p>
            On June 1, 2026, GitHub Copilot moved chat, agent mode, code review, and CLI
            usage from flat-fee plans to metered AI Credits, where one credit is $0.01
            and cost varies by model. The seat price — $10 for Pro, $39 for Pro+, $19 per
            user for Business, $39 per user for Enterprise — is no longer the ceiling on
            the bill; it&apos;s the floor. Inline completions are the only feature that
            stayed flat-fee.
          </p>
          <p>
            The economics of coding assistants make this expensive fast. Every chat turn
            resends the conversation so far, and every agent step resends system prompts,
            file trees, and diffs. Token volume grows with usage squared, not linearly —
            which is how a team paying $50 a month under flat pricing can project $3,000
            under metered billing with heavy agent use.
          </p>
          <p>
            The same mechanics are also why routing the workload through a gateway is
            cheaper: all that resent context is exactly what prompt caching absorbs,
            billing repeated input tokens at roughly a tenth of the normal rate. Add
            pass-through provider pricing with no per-seat fee, and the structural gap
            this calculator shows emerges — before you even consider routing lighter
            tasks to cheaper models.
          </p>
          <p>
            When the estimate looks right, the{" "}
            <Link to="/migration/github-copilot" className="font-medium text-blue-400 underline-offset-4 hover:underline">
              GitHub Copilot migration guide
            </Link>{" "}
            maps each Copilot workflow to its gateway-backed replacement, and the{" "}
            <Link to="/compare/github-copilot" className="font-medium text-blue-400 underline-offset-4 hover:underline">
              full comparison
            </Link>{" "}
            covers features beyond cost.
          </p>
        </div>
        <h3 className="mt-10 text-[16px] font-semibold text-[var(--admin-text)]">
          Assumptions behind the math
        </h3>
        <ul className="mt-4 list-disc space-y-2 pl-5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
          {ASSUMPTIONS.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </section>

      {/* ── FAQ ── */}
      <section>
        <div className="mb-6 text-center">
          <h2 className="text-[24px] font-semibold tracking-[-0.01em] text-[var(--admin-text)]">
            Frequently asked questions
          </h2>
          <p className="mx-auto mt-2 max-w-2xl text-[14px] text-[var(--admin-text-muted)]">
            Everything you need to know about estimating and capping your team&apos;s AI
            coding spend.
          </p>
        </div>
        <div className="space-y-3">
          {FAQ.map((item) => (
            <details
              key={item.question}
              className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] [&_summary::-webkit-details-marker]:hidden"
            >
              <summary className="flex cursor-pointer items-center justify-between gap-4 px-5 py-4 text-left text-[14px] font-medium text-[var(--admin-text)]">
                {item.question}
                <ChevronDown className="h-5 w-5 shrink-0 text-[var(--admin-text-dim)] transition-transform duration-200 group-open:rotate-180" />
              </summary>
              <p className="px-5 pb-5 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">
                {item.answer}
              </p>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
