// Testimonials — scrolling marquee rows of community quotes. The reference
// used a TweetCard component that fetched live tweets; here we inline static
// testimonial cards (no external dependency) to stay self-contained.

import { Star } from "lucide-react";
import { MarqueeContainer } from "./MarqueeContainer";

interface Testimonial {
  quote: string;
  author: string;
  handle: string;
}

const row1: Testimonial[] = [
  { quote: "Switched from OpenRouter in an afternoon. Cost analytics alone paid for the move.", author: "Alex Chen", handle: "@alexbuilds" },
  { quote: "BYOK with zero markup is unreal. We route 40M tokens/day through our own keys.", author: "Priya Nair", handle: "@priyacodes" },
  { quote: "Self-hosted it on a single box. One binary, every model. Genuinely impressive.", author: "Marcus Webb", handle: "@marcuswebb" },
  { quote: "The failover is instant. When OpenAI rate-limits us, Anthropic picks up seamlessly.", author: "Sara Lopez", handle: "@saralo" },
  { quote: "Finally a gateway that doesn't lock me into one provider's pricing.", author: "Dev Patel", handle: "@devpatel" },
];

const row2: Testimonial[] = [
  { quote: "Real-time latency dashboards caught a 3x regression before our users did.", author: "Jordan Kim", handle: "@jordankim" },
  { quote: "The virtual keys + budgets feature replaced a whole internal tool we were building.", author: "Riley Brooks", handle: "@rileyb" },
  { quote: "Drop-in compatible with the OpenAI SDK. Changed one line and shipped.", author: "Tom Wright", handle: "@tomwright" },
  { quote: "Weighted round-robin across our key pool smoothed out rate limits completely.", author: "Nina Costa", handle: "@ninacosta" },
];

function TestimonialCard({ t }: { t: Testimonial }) {
  return (
    <div className="w-80 shrink-0">
      <div className="w-full rounded-2xl border border-[var(--admin-border)] bg-[var(--admin-surface)] p-5 shadow-sm transition-shadow hover:shadow-md">
        <div className="mb-3 flex gap-0.5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Star key={i} className="size-3.5 fill-yellow-400 text-yellow-400" />
          ))}
        </div>
        <p className="mb-4 text-sm leading-relaxed text-[var(--admin-text)]">{t.quote}</p>
        <div className="flex items-center gap-2">
          <div className="flex size-8 items-center justify-center rounded-full bg-blue-500/10 text-xs font-semibold text-blue-400">
            {t.author.charAt(0)}
          </div>
          <div>
            <div className="text-xs font-medium text-[var(--admin-text)]">{t.author}</div>
            <div className="text-xs text-[var(--admin-text-dim)]">{t.handle}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Testimonials() {
  return (
    <section className="relative overflow-hidden bg-[var(--admin-surface-elevated)] py-24 md:py-32">
      <div className="relative">
        <div className="mx-auto mb-16 max-w-7xl px-6 lg:px-8">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-[var(--admin-text-muted)]">
            Community
          </p>
          <h2 className="text-4xl font-bold tracking-tight text-[var(--admin-text)] md:text-5xl">
            Trusted by developers worldwide
          </h2>
        </div>

        <div className="space-y-6">
          <MarqueeContainer>
            {row1.map((t) => (
              <TestimonialCard key={t.handle} t={t} />
            ))}
          </MarqueeContainer>

          <MarqueeContainer reverse>
            {row2.map((t) => (
              <TestimonialCard key={t.handle} t={t} />
            ))}
          </MarqueeContainer>
        </div>
      </div>
    </section>
  );
}
