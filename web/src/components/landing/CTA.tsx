// CTA — final call-to-action section. Converted from the llmgateway reference:
// next/link → react-router-dom Link, ShimmerButton → inline shimmer-styled
// anchor, framer-motion AnimatedGroup → CSS AnimatedGroup. Self-contained.

import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { AnimatedGroup } from "./AnimatedGroup";

export function CTA() {
  return (
    <section className="relative overflow-hidden py-32 md:py-40">
      {/* Gradient separator at top */}
      <div className="absolute left-0 right-0 top-0 h-px bg-gradient-to-r from-transparent via-[var(--admin-border)] to-transparent" />

      {/* Atmospheric background */}
      <div className="absolute inset-0 bg-gradient-to-b from-[var(--admin-bg)] via-[var(--admin-text)]/[0.02] to-[var(--admin-bg)]" />

      {/* Soft radial glow */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[400px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-500/[0.06] blur-3xl" />

      <div className="container relative mx-auto px-4">
        <div className="mx-auto max-w-3xl text-center">
          <AnimatedGroup preset="blur-slide" className="space-y-6">
            <h2 className="font-bold text-4xl tracking-tight text-[var(--admin-text)] md:text-5xl lg:text-6xl">
              Start routing requests
              <br />
              in 30 seconds
            </h2>
            <p className="mx-auto max-w-xl text-lg text-[var(--admin-text-muted)]">
              Join thousands of developers processing 100B+ tokens through wiwi.
              Free tier included, no credit card required.
            </p>
          </AnimatedGroup>

          <AnimatedGroup
            preset="blur-slide"
            className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row"
          >
            <Link
              to="/signup"
              className="wiwi-shimmer relative inline-flex w-full items-center justify-center gap-3 rounded-xl bg-blue-600 px-8 py-4 text-base font-medium text-white shadow-2xl shadow-blue-500/25 transition-colors hover:bg-blue-500 sm:w-auto"
            >
              Create Free Account
              <ArrowRight className="size-4" />
            </Link>
            <a
              href="https://github.com/shinmentakezo07/wiwia"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--admin-border)] bg-transparent px-8 py-4 text-base text-[var(--admin-text)] transition-colors hover:bg-white/[0.04] sm:w-auto"
            >
              Self-host wiwi
            </a>
          </AnimatedGroup>
        </div>
      </div>
    </section>
  );
}
