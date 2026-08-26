// Faq — accordion FAQ section. Replaces @radix-ui/react-accordion with a
// native <details>/<summary> element for a dependency-free, CSS-only accordion.
// Replaces @llmgateway/shared MARKETING_STATS with hardcoded values, and
// next/link with react-router-dom Link.

import { Plus, Minus } from "lucide-react";
import { Link } from "react-router-dom";
import { useState } from "react";

const MODELS_COUNT = "200+";
const PROVIDERS_COUNT = "40+";
const DATA_STORAGE_PRICE = "$5/mo";

const faqData = [
  {
    question: "What makes wiwi different from OpenRouter?",
    answer: `Unlike OpenRouter, wiwi offers: Full self-hosting under an open license — run the gateway entirely on your infra. Deeper, real-time cost & latency analytics for every request. Bring Your Own Keys for free. Flexible enterprise add-ons (dedicated shard, custom SLAs).`,
  },
  {
    question: "What models do you support?",
    answer: `We support ${MODELS_COUNT} models across ${PROVIDERS_COUNT} providers—including GPT-4o, Claude, Gemini, Llama, Mistral, and more. We add new releases within 48 hours of launch.`,
  },
  {
    question: "What is your uptime guarantee?",
    answer:
      "Our public status page posts real-time metrics. Enterprise instances come with a 99.9% uptime SLA; self-host installations depend on your infrastructure.",
  },
  {
    question: "How much does it cost?",
    answer: `Credits: Pay-as-you-go with a flat 5% platform fee. BYOK: Use your own provider API keys for free. Enterprise: Custom SLA, dedicated infrastructure, and volume discounts. Self-host: Deploy free forever. Optional full data retention is billed at ${DATA_STORAGE_PRICE} in both credits and BYOK modes.`,
  },
];

const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: faqData.map((item) => ({
    "@type": "Question",
    name: item.question,
    acceptedAnswer: { "@type": "Answer", text: item.answer },
  })),
};

function FaqItem({ index }: { index: number }) {
  const [open, setOpen] = useState(false);
  const item = faqData[index];
  return (
    <div className="border-b border-[var(--admin-border)]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-4 py-5 text-left"
      >
        <span className="text-lg font-medium text-[var(--admin-text)] md:text-xl">
          {item.question}
        </span>
        {open ? (
          <Minus className="size-[18px] shrink-0 text-[var(--admin-text-muted)]" />
        ) : (
          <Plus className="size-[18px] shrink-0 text-[var(--admin-text-muted)]" />
        )}
      </button>
      {open && (
        <div className="overflow-hidden text-base leading-relaxed text-[var(--admin-text-muted)]">
          <div className="border-l-2 border-[var(--admin-border)] pb-4 pl-4">
            {index === 0 && (
              <div>
                <p>Unlike OpenRouter, we offer:</p>
                <ul className="mt-2 list-disc space-y-1 pl-6">
                  <li>
                    Full <strong>self-hosting</strong> under an open license — run the gateway
                    entirely on your infra.
                  </li>
                  <li>
                    Deeper, real-time <strong>cost &amp; latency analytics</strong> for every
                    request
                  </li>
                  <li>
                    <strong>Bring Your Own Keys</strong> — use your own provider API keys for free
                  </li>
                  <li>
                    Flexible <strong>enterprise add-ons</strong> (dedicated shard, custom SLAs)
                  </li>
                </ul>
              </div>
            )}
            {index === 1 && (
              <div>
                {item.answer} Check the{" "}
                <Link to="/models" className="underline">
                  models page
                </Link>{" "}
                for the full list.
              </div>
            )}
            {index === 2 && (
              <div>
                Our public status page posts real-time metrics. Enterprise instances come with a{" "}
                <strong>99.9% uptime SLA</strong>; self-host installations depend on your
                infrastructure.
              </div>
            )}
            {index === 3 && (
              <div>
                <p>Our pricing is simple and transparent:</p>
                <ul className="mt-2 list-disc space-y-1 pl-6">
                  <li>
                    <strong>Credits — 5% fee:</strong> Pay-as-you-go credits to use any model with
                    a flat 5% platform fee on purchases.
                  </li>
                  <li>
                    <strong>Bring Your Own Keys — free:</strong> Use your own LLM provider API keys
                    and pay providers directly. Usage tracking and analytics included at no extra
                    cost.
                  </li>
                  <li>
                    <strong>Enterprise:</strong> Custom SLA, dedicated infrastructure,
                    bring-your-own cloud capacity, and volume discounts. Contact sales for a
                    tailored quote.
                  </li>
                  <li>
                    <strong>Self-host:</strong> Deploy the gateway on your own
                    infrastructure—free forever.
                  </li>
                </ul>
                <p className="mt-2">
                  Optional{" "}
                  <a
                    href="https://docs.example.com/features/data-retention#storage-pricing"
                    className="underline"
                  >
                    full data retention
                  </a>{" "}
                  is billed at {DATA_STORAGE_PRICE} in both credits and BYOK modes.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function Faq() {
  return (
    <section className="w-full bg-[var(--admin-bg)] py-20 md:py-32" id="faq">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }}
      />
      <div className="container mx-auto px-4 md:px-6">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-5 lg:gap-16">
          {/* Left column: sticky heading */}
          <div className="lg:col-span-2 lg:sticky lg:top-24 lg:self-start">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">
              FAQ
            </p>
            <h2 className="text-3xl font-bold tracking-tight text-[var(--admin-text)] md:text-4xl lg:text-5xl">
              Common questions
            </h2>
            <p className="mt-4 text-[var(--admin-text-muted)]">
              Everything you need to know about pricing, models, and getting started.
            </p>
            <p className="mt-6 text-sm text-[var(--admin-text-muted)]">
              Can't find an answer?{" "}
              <a
                href="mailto:contact@example.com"
                className="text-[var(--admin-text)] underline underline-offset-4"
              >
                Contact us
              </a>
            </p>
          </div>

          {/* Right column: accordion */}
          <div className="lg:col-span-3">
            <div className="w-full">
              {faqData.map((_, i) => (
                <FaqItem key={i} index={i} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
