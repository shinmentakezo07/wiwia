// PricingPlans — three-tier pricing table. Replaces next/navigation useRouter
// with react-router-dom useNavigate, usePostHog + useSessionStatus with a
// simple auth check via localStorage, and radix/shadcn Card/Button/Badge with
// inline divs styled via the admin design system tokens.

import { Check } from "lucide-react";
import { useNavigate } from "react-router-dom";

const plans = [
  {
    name: "Self-Host",
    description: "Host on your own infrastructure",
    price: "Free",
    features: [
      "100% free forever",
      "Full control over your data",
      "Host on your infrastructure",
      "No usage limits",
      "Community support",
      "Regular updates",
    ],
    cta: "View Documentation",
    popular: false,
  },
  {
    name: "Free",
    description: "Full-featured plan for everyone",
    price: "$0",
    features: [
      "Access to ALL models",
      "Pay with credits (5% fee)",
      "Bring Your Own Keys (free)",
      "30-day data retention",
      "Team Management",
      "Advanced Analytics",
      "Auto-routing & Vendor Selection",
      "Discord support",
    ],
    cta: "Get Started",
    popular: true,
  },
  {
    name: "Enterprise",
    description: "For large organizations with custom needs",
    price: "Custom",
    features: [
      "Everything in Free",
      "Unlimited seats",
      "Prioritized feature requests",
      "On-boarding assistance",
      "Unlimited data retention",
      "24/7 premium support",
      "Chat-App (incl. whitelabel)",
      "Single Sign-On (SSO)",
      "Volume discounts",
    ],
    cta: "Contact Sales",
    popular: false,
  },
];

export function PricingPlans() {
  const navigate = useNavigate();

  const handlePlanSelection = (planName: string) => {
    switch (planName) {
      case "Self-Host":
        navigate("/docs");
        return;
      case "Enterprise":
        navigate("/enterprise");
        return;
    }
    // Free plan: go to signup if not logged in, else dashboard
    const hasSession = !!localStorage.getItem("wiwi.user");
    if (!hasSession) {
      navigate("/signup");
    } else {
      navigate("/app");
    }
  };

  return (
    <section className="w-full bg-white/[0.015] py-12 md:py-24" id="pricing">
      <div className="container mx-auto px-4 md:px-6">
        <div className="mb-12 text-center">
          <span className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-[var(--admin-border)] bg-white/[0.03] px-3 py-1 text-xs font-medium text-[var(--admin-text-muted)]">
            Pricing
          </span>
          <h2 className="mb-4 text-3xl font-bold tracking-tighter text-[var(--admin-text)] sm:text-4xl md:text-5xl">
            Start for free, Scale with low fees
          </h2>
          <p className="mx-auto max-w-3xl text-xl text-[var(--admin-text-muted)]">
            All features included in our free plan. No hidden fees or surprises.
          </p>
        </div>

        <div className="mx-auto grid max-w-5xl grid-cols-1 gap-6 md:grid-cols-3">
          {plans.map((plan) => (
            <div
              key={plan.name}
              className={`relative flex flex-col rounded-2xl border bg-[var(--admin-surface)] p-6 ${
                plan.popular
                  ? "border-blue-500/40 shadow-lg shadow-blue-500/10"
                  : "border-[var(--admin-border)]"
              }`}
            >
              {plan.popular && (
                <div className="absolute -right-2 -top-2 -translate-y-2 translate-x-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white">
                    Recommended
                  </span>
                </div>
              )}
              <div className="mb-2">
                <h3 className="text-lg font-semibold text-[var(--admin-text)]">{plan.name}</h3>
                <p className="text-sm text-[var(--admin-text-muted)]">{plan.description}</p>
              </div>
              <div className="mt-4">
                <span className="text-3xl font-bold text-[var(--admin-text)]">{plan.price}</span>
                {plan.price !== "Custom" && plan.price !== "Free" && (
                  <span className="ml-1 text-[var(--admin-text-muted)]">forever</span>
                )}
              </div>
              <div className="mt-6 flex-grow">
                <ul className="space-y-2">
                  {plan.features.map((feature, i) => (
                    <li key={i} className="flex items-center">
                      <Check className="mr-2 size-4 shrink-0 text-green-500" />
                      <span className="text-sm text-[var(--admin-text)]">{feature}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="mt-6">
                <button
                  onClick={() => handlePlanSelection(plan.name)}
                  className={`w-full rounded-xl px-4 py-2.5 text-sm font-medium transition-colors ${
                    plan.popular
                      ? "bg-blue-600 text-white hover:bg-blue-500"
                      : "border border-[var(--admin-border)] bg-transparent text-[var(--admin-text)] hover:bg-white/[0.04]"
                  }`}
                >
                  {plan.cta}
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-12 text-center">
          <p className="text-[var(--admin-text-muted)]">
            All plans include access to our API, documentation, and community support.
            <br />
            Need a custom solution?{" "}
            <a
              href="mailto:contact@example.com"
              className="text-blue-400 hover:underline"
            >
              Contact our sales team
            </a>
            .
          </p>
        </div>
      </div>
    </section>
  );
}
