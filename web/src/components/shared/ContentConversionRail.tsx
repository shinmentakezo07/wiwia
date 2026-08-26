// ContentConversionRail — a sticky bottom rail that appears after the reader
// scrolls past the opening, offering a CTA to browse models. Ported from the
// Next.js reference's content-conversion-rail.tsx. Uses localStorage for
// dismissal and a scroll listener for reveal; no PostHog.

import { ArrowRight, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

type RailVariant = "devpass" | "gateway";

interface ContentConversionRailProps {
  variant?: RailVariant;
  /** Surface name for analytics — "blog", "guide", "timeline". */
  surface: string;
  /** Model slug to deep-link when the reader is on a page about one specific model. */
  model?: string;
}

const DISMISS_KEY = "wiwi-rail-dismissed";
const REVEAL_AT = 0.28;

export function ContentConversionRail({
  variant = "gateway",
  surface,
  model,
}: ContentConversionRailProps) {
  const [visible, setVisible] = useState(false);
  const [dismissed, setDismissed] = useState(true);
  const [cardOnScreen, setCardOnScreen] = useState(false);
  const shownRef = useRef(false);
  const location = useLocation();

  useEffect(() => {
    try {
      setDismissed(localStorage.getItem(DISMISS_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);

  useEffect(() => {
    if (dismissed) return;
    const onScroll = () => {
      const scrollable = document.body.scrollHeight - window.innerHeight;
      if (scrollable <= 0) return;
      setVisible(window.scrollY / scrollable >= REVEAL_AT);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [dismissed]);

  useEffect(() => {
    const cards = document.querySelectorAll("[data-inline-cta]");
    if (!cards.length || typeof IntersectionObserver === "undefined") return;
    const seen = new Set<Element>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) seen.add(entry.target);
          else seen.delete(entry.target);
        }
        setCardOnScreen(seen.size > 0);
      },
      { rootMargin: "-10% 0px" },
    );
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, []);

  const showing = visible && !dismissed && !cardOnScreen;

  useEffect(() => {
    if (showing && !shownRef.current) {
      shownRef.current = true;
      console.info("[analytics] conversion_rail_shown", { surface, variant });
    }
  }, [showing, surface, variant]);

  const dismiss = useCallback(() => {
    setDismissed(true);
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch {
      /* ignore */
    }
    console.info("[analytics] conversion_rail_dismissed", { surface, variant });
  }, [surface, variant]);

  const track = (cta: string) => {
    console.info("[analytics] cta_clicked", { location: `rail_${surface}`, cta, variant, path: location.pathname });
  };

  const isDevPass = variant === "devpass";
  const href = isDevPass ? "/pricing" : model ? `/models/${model}` : "/models";

  return (
    <div
      aria-hidden={!showing}
      className={`pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center pb-3 pl-3 pr-16 transition-[opacity,transform] duration-300 ease-out sm:px-3 sm:pb-4 ${
        showing ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"
      }`}
    >
      <div className="pointer-events-auto flex w-full max-w-2xl items-center gap-3 rounded-xl border border-dashed border-zinc-700/70 bg-zinc-900/90 px-3 py-2.5 backdrop-blur shadow-[0_8px_30px_-12px_rgba(0,0,0,0.35)] sm:gap-4 sm:px-4">
        <div className="min-w-0 flex-1">
          <div className="hidden font-mono text-[9px] uppercase tracking-[0.3em] text-zinc-500 sm:block">
            {isDevPass ? "DevPass" : "wiwi"}
          </div>
          <p className="truncate text-[13px] font-medium leading-snug text-[var(--admin-text)] sm:mt-0.5 sm:text-sm">
            {isDevPass ? "Every model, one flat rate" : "One key, every model"}
          </p>
        </div>

        <div className="hidden h-8 w-px shrink-0 border-l border-dashed border-zinc-700/70 sm:block" />

        <Link
          to={href}
          onClick={() => track(isDevPass ? "get_devpass" : "browse_models")}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-black dark:hover:bg-zinc-200"
        >
          <span className="sm:hidden">{isDevPass ? "Plans" : "Models"}</span>
          <span className="hidden sm:inline">
            {isDevPass ? "See plans" : model ? "Try this model" : "Browse models"}
          </span>
          <ArrowRight className="h-3.5 w-3.5 transition-transform duration-200 ease-out group-hover:translate-x-0.5" />
        </Link>

        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss"
          className="shrink-0 rounded-md p-1 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-[var(--admin-text)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
