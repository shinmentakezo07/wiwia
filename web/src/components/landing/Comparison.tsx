// Comparison — feature comparison table. Replaces next/link with
// react-router-dom Link, radix Badge/Button with inline elements, and the
// AuthLink wrapper with a direct Link to /signup.

import { Check, X } from "lucide-react";
import { Link } from "react-router-dom";

const comparisonData = [
  {
    category: "Pricing & Fees",
    features: [
      {
        title: "Credits pricing",
        description: "Pay-as-you-go with credits",
        wiwi: "Flat 5% fee on credit purchases",
        openrouter: "5.5% platform fee",
      },
      {
        title: "Bring Your Own Keys",
        description: "Use your own provider API keys",
        wiwi: "Free — pay providers directly",
        openrouter: "Free to $25k/mo PAYG ($200k enterprise), then 5%",
      },
      {
        title: "Self-hosting option",
        description: "Deploy on your infrastructure for free (See license)",
        wiwi: "Free for non-commercial use",
        openrouter: false as boolean | string,
      },
    ],
  },
  {
    category: "Analytics & Monitoring",
    features: [
      {
        title: "Real-time cost analytics",
        description: "Detailed cost tracking for every request",
        wiwi: true as boolean | string,
        openrouter: true as boolean | string,
      },
      {
        title: "Latency analytics",
        description: "Real-time performance monitoring",
        wiwi: true as boolean | string,
        openrouter: "Basic",
      },
      {
        title: "Request-level insights",
        description: "Granular analytics for each API call",
        wiwi: true as boolean | string,
        openrouter: true as boolean | string,
      },
      {
        title: "Usage dashboard",
        description: "Comprehensive usage metrics",
        wiwi: true as boolean | string,
        openrouter: true as boolean | string,
      },
    ],
  },
  {
    category: "Reliability & Support",
    features: [
      {
        title: "Uptime SLA",
        description: "Guaranteed uptime for managed instances",
        wiwi: "99.9%",
        openrouter: "Enterprise only",
      },
      {
        title: "Failover support",
        description: "Automatic failover to backup providers",
        wiwi: true as boolean | string,
        openrouter: true as boolean | string,
      },
      {
        title: "Load balancing",
        description: "Distribute requests across providers",
        wiwi: true as boolean | string,
        openrouter: true as boolean | string,
      },
      {
        title: "Priority support",
        description: "Dedicated support for paid plans",
        wiwi: "Enterprise",
        openrouter: "Enterprise only",
      },
    ],
  },
];

function renderFeatureValue(value: boolean | string) {
  if (typeof value === "boolean") {
    return value ? (
      <Check className="size-5 text-green-400" />
    ) : (
      <X className="size-5 text-red-400" />
    );
  }
  return <span className="text-sm font-medium text-[var(--admin-text)]">{value}</span>;
}

export function Comparison() {
  return (
    <section className="w-full bg-[var(--admin-bg)] py-12 md:py-24 lg:py-32">
      <div className="container mx-auto max-w-5xl px-4 md:px-6">
        <div className="mb-12 text-center">
          <span className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.03] px-3 py-1 text-xs font-medium text-[var(--admin-text-muted)]">
            Compare platforms
          </span>
          <h2 className="mb-2 text-3xl font-bold tracking-tight text-[var(--admin-text)]">
            Find the perfect fit
          </h2>
          <p className="text-[var(--admin-text-muted)]">
            Compare wiwi and OpenRouter features side by side
          </p>
        </div>

        <div className="mb-8 rounded-lg border border-blue-500/20 bg-blue-500/5 p-6">
          <h3 className="mb-3 text-lg font-bold text-blue-400">Why choose wiwi?</h3>
          <div className="grid gap-4 text-sm md:grid-cols-2">
            <div className="flex items-start gap-2">
              <Check className="mt-0.5 size-4 shrink-0 text-green-400" />
              <span className="text-[var(--admin-text)]">
                <strong>Bring Your Own Keys</strong> at no extra cost
              </span>
            </div>
            <div className="flex items-start gap-2">
              <Check className="mt-0.5 size-4 shrink-0 text-green-400" />
              <span className="text-[var(--admin-text)]">
                <strong>Real-time analytics</strong> for cost &amp; latency optimization
              </span>
            </div>
            <div className="flex items-start gap-2">
              <Check className="mt-0.5 size-4 shrink-0 text-green-400" />
              <span className="text-[var(--admin-text)]">
                <strong>Can be self hosted</strong> for complete control
              </span>
            </div>
            <div className="flex items-start gap-2">
              <Check className="mt-0.5 size-4 shrink-0 text-green-400" />
              <span className="text-[var(--admin-text)]">
                <strong>99.9% uptime SLA</strong> with enterprise support
              </span>
            </div>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-[var(--admin-border)] bg-[var(--admin-surface)] shadow-sm">
          {/* Header row */}
          <div className="grid grid-cols-1 gap-4 border-b border-[var(--admin-border)] bg-white/[0.01] p-4 sm:p-6 md:grid-cols-3">
            <div className="hidden md:block" />
            <div className="text-center">
              <div className="h-full rounded-lg border-2 border-blue-500 bg-[var(--admin-bg)] p-4 shadow-sm">
                <h3 className="mb-1 text-lg font-bold text-[var(--admin-text)]">wiwi</h3>
                <p className="mb-2 text-sm text-[var(--admin-text-muted)]">OPEN &amp; FLEXIBLE</p>
                <p className="text-2xl font-bold text-blue-400">From $0</p>
                <p className="mt-1 text-xs text-[var(--admin-text-muted)]">Self-host free forever</p>
              </div>
            </div>
            <div className="text-center">
              <div className="h-full rounded-lg border border-[var(--admin-border)] bg-[var(--admin-bg)] p-4">
                <h3 className="mb-1 text-lg font-bold text-[var(--admin-text)]">OpenRouter</h3>
                <p className="mb-2 text-sm text-[var(--admin-text-muted)]">CLOSED &amp; 5.5% fee</p>
                <p className="text-2xl font-bold text-[var(--admin-text)]">From $0</p>
                <p className="mt-1 text-xs text-[var(--admin-text-muted)]">Credit-based pricing</p>
              </div>
            </div>
          </div>

          {/* Category rows */}
          {comparisonData.map((category, categoryIndex) => (
            <div key={categoryIndex}>
              {categoryIndex > 0 && <div className="border-t-2 border-[var(--admin-border)]/50" />}
              {category.features.map((feature, featureIndex) => (
                <div
                  key={featureIndex}
                  className="grid grid-cols-3 gap-4 border-b border-[var(--admin-border)]/50 p-6 transition-colors hover:bg-white/[0.01]"
                >
                  <div>
                    <h4 className="mb-1 font-semibold text-[var(--admin-text)]">{feature.title}</h4>
                    <p className="text-sm text-[var(--admin-text-muted)]">{feature.description}</p>
                  </div>
                  <div className="flex items-center justify-center">
                    {renderFeatureValue(feature.wiwi)}
                  </div>
                  <div className="flex items-center justify-center">
                    {renderFeatureValue(feature.openrouter)}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="mt-8 text-center">
          <div className="flex flex-col justify-center gap-4 sm:flex-row">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center rounded-xl bg-blue-600 px-6 py-3 text-sm font-medium text-white transition-colors hover:bg-blue-500"
            >
              Start Free with wiwi
            </Link>
            <Link
              to="/pricing"
              className="inline-flex items-center justify-center rounded-xl border border-[var(--admin-border)] bg-transparent px-6 py-3 text-sm font-medium text-[var(--admin-text)] transition-colors hover:bg-white/[0.04]"
            >
              View Pricing Details
            </Link>
          </div>
          <p className="mt-3 text-sm text-[var(--admin-text-muted)]">
            No credit card required • Self-host option available • Enterprise support included
          </p>
        </div>
      </div>
    </section>
  );
}
