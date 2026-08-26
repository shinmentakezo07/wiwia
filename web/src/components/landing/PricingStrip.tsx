// PricingStrip — three-option pricing summary. Replaces @llmgateway/shared
// MARKETING_STATS with hardcoded values, next/link with react-router-dom,
// framer-motion AnimatedGroup with CSS AnimatedGroup.

import { ArrowRight, KeyRound, Server, Wallet } from "lucide-react";
import { Link } from "react-router-dom";
import { AnimatedGroup } from "./AnimatedGroup";

const options = [
  {
    icon: Wallet,
    name: "Credits",
    price: "5% flat fee",
    description:
      "Pay-as-you-go credits for any model at provider rates, with a flat platform fee on top-ups. No subscription, no markup on tokens.",
  },
  {
    icon: KeyRound,
    name: "Bring your own keys",
    price: "Free",
    description:
      "Route through your own provider API keys and pay providers directly. Routing, tracking, and analytics included at no cost.",
  },
  {
    icon: Server,
    name: "Self-host",
    price: "Free forever",
    description:
      "Deploy the open-licensed gateway on your own infrastructure. The full routing layer, yours to run.",
  },
];

export function PricingStrip() {
  return (
    <section className="relative py-20 md:py-28">
      <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />
      <div className="container mx-auto px-4">
        <AnimatedGroup preset="blur-slide" className="mb-12 text-center">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">
            Pricing
          </p>
          <h2 className="text-3xl font-bold tracking-tight text-[var(--admin-text)] md:text-4xl lg:text-5xl">
            Three ways to run it. Two are free.
          </h2>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-[var(--admin-text-muted)]">
            No seats, no minimums, no token markup. Start free and only pay when you
            top up credits.
          </p>
        </AnimatedGroup>

        <AnimatedGroup
          preset="slide"
          className="mx-auto grid max-w-5xl grid-cols-1 gap-4 md:grid-cols-3"
        >
          {options.map((option) => (
            <div
              key={option.name}
              className="group rounded-xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-6 transition-all duration-300 hover:border-blue-500/30 hover:shadow-md hover:shadow-blue-500/5"
            >
              <div className="mb-4 flex size-10 items-center justify-center rounded-lg border border-[var(--admin-border)] bg-white/[0.02] transition-colors group-hover:border-blue-500/30 group-hover:bg-blue-500/5">
                <option.icon className="size-5 text-[var(--admin-text-muted)] transition-colors group-hover:text-blue-500" />
              </div>
              <div className="mb-1.5 flex items-baseline justify-between gap-2">
                <h3 className="text-base font-semibold tracking-tight text-[var(--admin-text)]">
                  {option.name}
                </h3>
                <span className="font-mono text-sm font-semibold text-blue-500">
                  {option.price}
                </span>
              </div>
              <p className="text-sm leading-relaxed text-[var(--admin-text-muted)]">
                {option.description}
              </p>
            </div>
          ))}
        </AnimatedGroup>

        <AnimatedGroup preset="fade" className="mt-8 text-center">
          <Link
            to="/pricing"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--admin-text-muted)] transition-colors hover:text-[var(--admin-text)]"
          >
            Compare all plans, including Enterprise
            <ArrowRight className="size-3.5" />
          </Link>
        </AnimatedGroup>
      </div>
    </section>
  );
}
