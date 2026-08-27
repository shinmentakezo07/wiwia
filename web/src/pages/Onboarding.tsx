// Onboarding — new user onboarding wizard. Adapted from the llmgateway.io
// onboarding page, simplified to a static multi-step guide in the dark design system.

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  Code2,
  Database,
  KeyRound,
  Rocket,
  Server,
  Terminal,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button, Card } from "@/components/ui";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

const STEPS: { icon: LucideIcon; title: string; description: string; detail: string }[] = [
  {
    icon: KeyRound,
    title: "Create your first API key",
    description: "Generate a virtual key to start routing requests through the gateway.",
    detail: "Navigate to Virtual Keys and click New Key. Set a budget, rate limit, and choose which models the key can access.",
  },
  {
    icon: Server,
    title: "Connect a provider",
    description: "Add your OpenAI, Anthropic, Gemini, or OpenRouter API keys.",
    detail: "Provider keys enter via environment interpolation in your config — they are never stored in plaintext. The gateway routes to them automatically.",
  },
  {
    icon: Code2,
    title: "Point your app at the gateway",
    description: "Change one line: the base URL.",
    detail: "Keep your existing OpenAI SDK. Just set baseURL to your gateway endpoint and apiKey to your virtual key. Everything else stays the same.",
  },
  {
    icon: Database,
    title: "Set up your model list",
    description: "Map model names your app requests to provider-native model IDs.",
    detail: "Define model_name aliases in your config. When your app requests gpt-5, the gateway routes to the right provider account automatically.",
  },
  {
    icon: Users,
    title: "Invite your team",
    description: "Add team members and assign roles.",
    detail: "Create user accounts with admin or member roles. Each member gets their own API keys with shared access to the gateway's model list.",
  },
  {
    icon: Rocket,
    title: "Ship to production",
    description: "Deploy with confidence — failover, budgets, and logs are built in.",
    detail: "The gateway handles retries, cooldowns, and fallbacks automatically. Monitor usage and cost from the admin dashboard.",
  },
];

export function OnboardingPage() {
  const [current, setCurrent] = useState(0);

  return (
    <div className="mx-auto max-w-2xl space-y-6 pb-16">
      {/* ── header ── */}
      <section className="text-center">
        <Terminal className="mx-auto h-10 w-10 text-blue-400" />
        <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-[var(--admin-text)]">
          Welcome to the{" "}
          <span className="bg-gradient-to-r from-blue-400 to-fuchsia-400 bg-clip-text text-transparent">
            gateway
          </span>
        </h1>
        <p className="mx-auto mt-3 max-w-md text-[15px] leading-relaxed text-[var(--admin-text-muted)]">
          Let&apos;s get you set up. Follow these steps to route your first request
          through the gateway.
        </p>
      </section>

      {/* ── step progress ── */}
      <section>
        <div className="mb-4 flex items-center justify-center gap-2">
          {STEPS.map((_, index) => (
            <button
              key={index}
              onClick={() => setCurrent(index)}
              className={`h-2 rounded-full transition-all ${
                index === current ? "w-8 bg-blue-400" : index < current ? "w-2 bg-blue-400/40" : "w-2 bg-white/[0.08]"
              }`}
              aria-label={`Step ${index + 1}`}
            />
          ))}
        </div>
        <Card className="p-6">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            const isActive = index === current;
            const isDone = index < current;
            return (
              <div
                key={step.title}
                className={isActive ? "block" : "hidden"}
              >
                <div className="mb-4 flex items-center gap-3">
                  <div
                    className={`flex h-12 w-12 items-center justify-center rounded-xl ${
                      isActive ? "bg-blue-500/10 text-blue-400" : isDone ? "bg-emerald-500/10 text-emerald-400" : "bg-white/[0.02] text-[var(--admin-text-dim)]"
                    }`}
                  >
                    {isDone ? <Check className="h-6 w-6" /> : <Icon className="h-6 w-6" />}
                  </div>
                  <div>
                    <span className="font-mono text-[12px] text-[var(--admin-text-dim)]" style={{ fontFamily: MONO }}>
                      Step {index + 1} of {STEPS.length}
                    </span>
                    <h2 className="text-[18px] font-semibold text-[var(--admin-text)]">{step.title}</h2>
                  </div>
                </div>
                <p className="text-[14px] leading-relaxed text-[var(--admin-text-muted)]">{step.description}</p>
                <p className="mt-3 text-[13px] leading-relaxed text-[var(--admin-text-muted)]">{step.detail}</p>
                <div className="mt-6 flex items-center justify-between">
                  <Button
                    variant="ghost"
                    onClick={() => setCurrent(Math.max(0, current - 1))}
                    disabled={current === 0}
                  >
                    Back
                  </Button>
                  {current < STEPS.length - 1 ? (
                    <Button onClick={() => setCurrent(current + 1)}>
                      Next
                      <ArrowRight size={16} className="ml-2" />
                    </Button>
                  ) : (
                    <Link
                      to="/console"
                      className="inline-flex items-center justify-center gap-2 rounded-[10px] bg-gradient-to-b from-brand-500 to-brand-700 px-5 py-2.5 text-[14px] font-medium text-white transition-[filter] hover:brightness-110"
                    >
                      Go to dashboard
                      <ArrowRight size={16} />
                    </Link>
                  )}
                </div>
              </div>
            );
          })}
        </Card>
      </section>

      {/* ── overview ── */}
      <section>
        <h2 className="mb-4 text-center text-[18px] font-semibold text-[var(--admin-text)]">
          All steps
        </h2>
        <div className="space-y-2">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <button
                key={step.title}
                onClick={() => setCurrent(index)}
                className="group flex w-full items-center gap-3 rounded-[10px] border border-[var(--admin-border)] bg-[var(--admin-surface)] px-4 py-3 text-left transition-colors hover:border-[var(--admin-border-hover)]"
              >
                <div
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
                    index < current ? "bg-emerald-500/10 text-emerald-400" : index === current ? "bg-blue-500/10 text-blue-400" : "bg-white/[0.02] text-[var(--admin-text-dim)]"
                  }`}
                >
                  {index < current ? <Check size={16} /> : <Icon size={16} />}
                </div>
                <div className="min-w-0 flex-1">
                  <span className="text-[14px] font-medium text-[var(--admin-text)]">{step.title}</span>
                </div>
                <ArrowRight size={14} className="text-[var(--admin-text-dim)] transition-transform group-hover:translate-x-0.5" />
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
