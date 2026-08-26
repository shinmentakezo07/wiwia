// Compare page sub-components — ported from the Next.js reference's
// components/compare/ directory: compare-faq, competitor-icons, hero-compare.
// All self-contained, using react-router and the dark admin design system.

import { PlusIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

// ── Competitor icons (inherit currentColor) ────────────────────────────────

const iconClass = "h-6 w-6";

function OpenRouterIcon() {
  return (
    <svg fill="currentColor" fillRule="evenodd" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" className={iconClass} aria-hidden="true">
      <path d="m16.804 1.957 7.22 4.105v.087L16.73 10.21l.017-2.117-.821-.03c-1.059-.028-1.611.002-2.268.11-1.064.175-2.038.577-3.147 1.352L8.345 11.03c-.284.195-.495.336-.68.455l-.515.322-.397.234.385.23.53.338c.476.314 1.17.796 2.701 1.866 1.11.775 2.083 1.177 3.147 1.352l.3.045c.694.091 1.375.094 2.825.033l.022-2.159 7.22 4.105v.087L16.589 22l.014-1.862-.635.022c-1.386.042-2.137.002-3.138-.162-1.694-.28-3.26-.926-4.881-2.059l-2.158-1.5a21.997 21.997 0 0 0-.755-.498l-.467-.28a55.927 55.927 0 0 0-.76-.43C2.908 14.73.563 14.116 0 14.116V9.888l.14.004c.564-.007 2.91-.622 3.809-1.124l1.016-.58.438-.274c.428-.28 1.072-.726 2.686-1.853 1.621-1.133 3.186-1.78 4.881-2.059 1.152-.19 1.974-.213 3.814-.138z" />
    </svg>
  );
}

function VercelIcon() {
  return (
    <svg viewBox="0 0 76 65" fill="currentColor" className={iconClass} aria-hidden="true">
      <path d="M37.5274 0L75.0548 65H0L37.5274 0Z" />
    </svg>
  );
}

function LiteLLMIcon() {
  return <span className="text-xl leading-none" aria-hidden="true">🚅</span>;
}

function PortkeyIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 180 180" className={iconClass} aria-hidden="true">
      <path fill="url(#portkey-compare-hub-gradient)" d="M109.063 7.5c14.782 0 28.37 7.992 35.766 20.851l23.12 40.191.346.614c7.159 12.942 7.078 28.784-.258 41.663l-23.179 40.68c-7.374 12.944-21.01 21.001-35.855 21.001H64.215c-14.95 0-28.669-8.17-36.004-21.26l-22.79-40.68c-7.256-12.951-7.227-28.838.082-41.759l22.738-40.19C35.598 15.604 49.266 7.5 64.156 7.5zM64.156 28.05c-7.392 0-14.312 4.021-18.088 10.696L23.33 78.936c-3.767 6.659-3.783 14.88-.044 21.556l22.797 40.687.178.314c3.803 6.531 10.647 10.457 17.953 10.457h44.788c7.37 0 14.274-3.997 18.057-10.639l23.173-40.681c3.842-6.743 3.825-15.098-.044-21.825l-23.113-40.197c-3.794-6.597-10.674-10.558-18.013-10.558zm25.44 22.11c4.268-3.54 10.597-3.037 14.256 1.172l25.171 28.956.223.263a14.81 14.81 0 0 1-.223 19.16l-25.171 28.957c-3.659 4.209-9.988 4.712-14.255 1.172l-.202-.172c-4.268-3.728-4.71-10.222-.991-14.499L110.284 90l-21.88-25.169c-3.718-4.277-3.277-10.771.99-14.5l.203-.17Z" />
      <defs>
        <linearGradient id="portkey-compare-hub-gradient" x1="-92.51" x2="194.256" y1="52.188" y2="216.739" gradientUnits="userSpaceOnUse">
          <stop offset=".173" stopColor="#00a3ff" />
          <stop offset=".899" stopColor="#ff0f00" />
        </linearGradient>
      </defs>
    </svg>
  );
}

function GitHubCopilotIcon() {
  return (
    <svg fill="currentColor" fillRule="evenodd" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" className={iconClass} aria-hidden="true">
      <path d="M19.245 5.364c1.322 1.36 1.877 3.216 2.11 5.817.622 0 1.2.135 1.592.654l.73.964c.21.278.323.61.323.955v2.62c0 .339-.173.669-.453.868C20.239 19.602 16.157 21.5 12 21.5c-4.6 0-9.205-2.583-11.547-4.258-.28-.2-.452-.53-.453-.868v-2.62c0-.345.113-.679.321-.956l.73-.963c.392-.517.974-.654 1.593-.654l.029-.297c.25-2.446.81-4.213 2.082-5.52 2.461-2.54 5.71-2.851 7.146-2.864h.198c1.436.013 4.685.323 7.146 2.864m-7.244 4.328c-.284 0-.613.016-.962.05-.123.447-.305.85-.57 1.108-1.05 1.023-2.316 1.18-2.994 1.18-.638 0-1.306-.13-1.851-.464-.516.165-1.012.403-1.044.996a65.882 65.882 0 0 0-.063 2.884l-.002.48c-.002.563-.005 1.126-.013 1.69.002.326.204.63.51.765 2.482 1.102 4.83 1.657 6.99 1.657 2.156 0 4.504-.555 6.985-1.657a.854.854 0 0 0 .51-.766c.03-1.682.006-3.372-.076-5.053-.031-.596-.528-.83-1.046-.996-.546.333-1.212.464-1.85.464-.677 0-1.942-.157-2.993-1.18-.266-.258-.447-.661-.57-1.108-.32-.032-.64-.049-.96-.05zm-2.525 4.013c.539 0 .976.426.976.95v1.753c0 .525-.437.95-.976.95a.964.964 0 0 1-.976-.95v-1.752c0-.525.437-.951.976-.951m5 0c.539 0 .976.426.976.95v1.753c0 .525-.437.95-.976.95a.964.964 0 0 1-.976-.95v-1.752c0-.525.437-.951.976-.951" />
    </svg>
  );
}

const icons: Record<string, () => React.JSX.Element> = {
  "open-router": OpenRouterIcon,
  portkey: PortkeyIcon,
  litellm: LiteLLMIcon,
  "vercel-ai-gateway": VercelIcon,
  "github-copilot": GitHubCopilotIcon,
};

export function CompetitorIcon({ slug }: { slug: string }) {
  const Icon = icons[slug];
  return Icon ? <Icon /> : null;
}

// ── CompareFaq ─────────────────────────────────────────────────────────────

export interface CompareFaqItem {
  question: string;
  answer: string;
}

interface CompareFaqProps {
  heading: string;
  description?: string;
  faqs: CompareFaqItem[];
}

export function CompareFaq({ heading, description, faqs }: CompareFaqProps) {
  const [openItem, setOpenItem] = useState<string | null>("item-1");
  const faqSchema = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqs.map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: { "@type": "Answer", text: item.answer },
    })),
  };

  return (
    <section className="w-full bg-[var(--admin-bg)] py-20 md:py-28" id="faq">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqSchema) }} />
      <div className="container mx-auto px-4 md:px-6">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-5 lg:gap-16">
          <div className="self-start lg:sticky lg:top-24 lg:col-span-2">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">FAQ</p>
            <h2 className="text-3xl font-bold tracking-tight md:text-4xl lg:text-5xl">{heading}</h2>
            {description && <p className="mt-4 text-[var(--admin-text-muted)]">{description}</p>}
            <p className="mt-6 text-sm text-[var(--admin-text-muted)]">
              Can&apos;t find an answer?{" "}
              <a href="mailto:contact@example.com" className="text-blue-400 underline underline-offset-4">Contact us</a>
            </p>
          </div>
          <div className="lg:col-span-3">
            {faqs.map((item, index) => {
              const value = `item-${index + 1}`;
              const isOpen = openItem === value;
              return (
                <div key={item.question} className="border-b border-[var(--admin-border)] py-5">
                  <button
                    type="button"
                    onClick={() => setOpenItem(isOpen ? null : value)}
                    className="flex w-full items-center justify-between gap-4 py-2 text-left text-lg font-medium text-[var(--admin-text)] transition-colors"
                  >
                    {item.question}
                    <PlusIcon
                      size={18}
                      className={`shrink-0 opacity-60 transition-transform duration-200 ${isOpen ? "rotate-45" : ""}`}
                      aria-hidden="true"
                    />
                  </button>
                  {isOpen && (
                    <div className="border-l-2 border-[var(--admin-border)] pl-4 pb-2 text-base leading-relaxed text-[var(--admin-text-muted)]">
                      {item.answer}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

// ── HeroCompare ────────────────────────────────────────────────────────────

interface HeroContent {
  heading: string;
  description: string;
  badges?: string[];
  cta: {
    primary: { text: string; href: string; external?: boolean };
    secondary: { text: string; href: string; external?: boolean };
  };
}

interface HeroCompareProps {
  content?: HeroContent;
}

const defaultContent: HeroContent = {
  heading: "Why Choose wiwi Over OpenRouter?",
  description:
    "Compare our unified API gateway with advanced routing, analytics, and cost optimization against OpenRouter's basic proxy service.",
  badges: ["Advanced Analytics", "Smart Routing", "Cost Optimization", "Enterprise Ready"],
  cta: {
    primary: { text: "Start for Free", href: "/signup" },
    secondary: { text: "View Documentation", href: "/docs", external: true },
  },
};

export function HeroCompare({ content }: HeroCompareProps) {
  const heroContent = { ...defaultContent, ...content };
  return (
    <main className="overflow-hidden">
      <section>
        <div className="relative pb-10 pt-24 md:pb-24 md:pt-36">
          <div className="mx-auto max-w-7xl px-6">
            <div className="mx-auto text-center">
              <h1 className="mx-auto mt-8 max-w-4xl text-2xl md:text-7xl lg:mt-16 xl:text-[5.25rem]">
                {heroContent.heading}
              </h1>
              <p className="mx-auto mt-8 max-w-2xl text-sm text-[var(--admin-text-muted)] md:text-lg">
                {heroContent.description}
              </p>
              {heroContent.badges && heroContent.badges.length > 0 && (
                <div className="mt-8 flex flex-wrap justify-center gap-2">
                  {heroContent.badges.map((badge, index) => (
                    <span
                      key={index}
                      className="inline-flex items-center rounded-full bg-blue-500/10 px-3 py-1 text-xs font-medium text-blue-400 ring-1 ring-inset ring-blue-500/20"
                    >
                      {badge}
                    </span>
                  ))}
                </div>
              )}
              <div className="mt-12 flex flex-col items-center justify-center gap-2 md:flex-row">
                <div className="rounded-[14px] border border-[var(--admin-border)] p-0.5">
                  {heroContent.cta.primary.external ? (
                    <a
                      href={heroContent.cta.primary.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="admin-btn admin-btn-primary"
                    >
                      <span className="whitespace-nowrap">{heroContent.cta.primary.text}</span>
                    </a>
                  ) : (
                    <Link to={heroContent.cta.primary.href} className="admin-btn admin-btn-primary">
                      <span className="whitespace-nowrap">{heroContent.cta.primary.text}</span>
                    </Link>
                  )}
                </div>
                {heroContent.cta.secondary.external ? (
                  <a
                    href={heroContent.cta.secondary.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="admin-btn admin-btn-ghost"
                  >
                    <span className="whitespace-nowrap">{heroContent.cta.secondary.text}</span>
                  </a>
                ) : (
                  <Link to={heroContent.cta.secondary.href} className="admin-btn admin-btn-ghost">
                    <span className="whitespace-nowrap">{heroContent.cta.secondary.text}</span>
                  </Link>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
